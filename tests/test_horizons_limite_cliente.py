"""El límite por cliente no se deja engañar por X-Forwarded-For falso y no crece sin tope."""
import pytest
from starlette.requests import Request

from app.core import config as c
from app.utils import auditoria_util
from tests.test_horizons import OK, api, fake, get  # noqa: F401  (fixtures y ayudas de las pruebas de /api/horizons)
from app.services import horizons as service


def _request(forwarded: str | None, host: str = "10.0.0.9") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "headers": headers, "client": (host, 1234)})


@pytest.mark.parametrize("cabecera, hops, esperada", [
    ("1.1.1.1, 2.2.2.2, 3.3.3.3", 1, "3.3.3.3"),      # lo falso va al principio; el balanceador añade la real al final
    ("1.1.1.1, 2.2.2.2, 3.3.3.3", 2, "2.2.2.2"),
    ("9.9.9.9", 1, "9.9.9.9"),
    ("9.9.9.9", 3, "9.9.9.9"),                         # menos entradas que saltos: la primera
    (" , ", 1, "10.0.0.9"),                            # cabecera vacía: el socket
    (None, 1, "10.0.0.9"),
])
def test_ip_confiable(monkeypatch, cabecera, hops, esperada):
    monkeypatch.setenv(c.ENV_TRUSTED_PROXY_HOPS, str(hops))
    assert auditoria_util.trusted_client_ip(_request(cabecera)) == esperada


def test_saltos_invalidos_vuelven_al_valor_por_defecto(monkeypatch):
    monkeypatch.setenv(c.ENV_TRUSTED_PROXY_HOPS, "abc")
    assert c.get_trusted_proxy_hops() == c.DEFAULT_TRUSTED_PROXY_HOPS


def test_falsear_el_principio_de_la_cabecera_no_evita_el_limite(api, monkeypatch):
    monkeypatch.setattr(service, "_client_limiter", service.SlidingWindowLimiter(2, 60.0))
    estados = [api.get("/api/horizons", params={**OK, "lat": -80 - i}, headers={"X-Forwarded-For": f"{i}.{i}.{i}.{i}, 8.8.8.8"}).status_code
               for i in range(4)]
    assert estados == [200, 200, 429, 429]


def test_miles_de_ips_falsas_no_hacen_crecer_la_memoria(api, monkeypatch):
    limitador = service.SlidingWindowLimiter(1000, 60.0, max_keys=40)
    monkeypatch.setattr(service, "_client_limiter", limitador)
    monkeypatch.setattr(service, "_global_limiter", service.SlidingWindowLimiter(10**9, 60.0))
    for i in range(300):
        api.get("/api/horizons", params={**OK, "lat": -80 - (i % 9) / 10, "lon": i / 10}, headers={"X-Forwarded-For": f"10.{i // 250}.{i % 250}.1"})
    assert len(limitador) <= 40

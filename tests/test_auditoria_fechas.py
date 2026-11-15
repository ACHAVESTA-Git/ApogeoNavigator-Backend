"""Fechas de /api/auditoria: con o sin zona horaria, nunca un 500 (la zona ausente se interpreta como UTC)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import config as c
from app.main import app
from app.utils import auditoria_util as au
from app.utils.dates import as_utc
from tests.conftest import API_KEY, fixture_bytes, upload

KEY = {"X-API-Key": API_KEY}
UTC = timezone.utc


@pytest.fixture
def api(bd, data_dir, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    au.set_firmante()
    client = TestClient(app)
    assert upload(client, "sites", fixture_bytes("sites_limites.csv"), "sites.csv").status_code == 200     # deja 1 evento
    return client


def _momento(client) -> datetime:
    return as_utc(datetime.fromisoformat(client.get("/api/auditoria", headers=KEY).json()["items"][0]["ts_utc"]))


def _total(client, **params) -> int:
    r = client.get("/api/auditoria", params=params, headers=KEY)
    assert r.status_code == 200, r.text
    return r.json()["total"]


# ------------------------------------------------------------------ as_utc
def test_as_utc_interpreta_una_fecha_sin_zona_como_utc():
    assert as_utc(datetime(2026, 12, 1, 8, 0)) == datetime(2026, 12, 1, 8, 0, tzinfo=UTC)


def test_as_utc_convierte_las_fechas_con_zona():
    peru = timezone(timedelta(hours=-5))
    assert as_utc(datetime(2026, 12, 1, 8, 0, tzinfo=peru)) == datetime(2026, 12, 1, 13, 0, tzinfo=UTC)
    assert as_utc(datetime(2026, 12, 1, 8, 0, tzinfo=peru)).utcoffset() == timedelta(0)


def test_as_utc_es_idempotente():
    fecha = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
    assert as_utc(as_utc(fecha)) == fecha


# ------------------------------------------------------------------ el bug: zona en una fecha y no en la otra
@pytest.mark.parametrize("desde,hasta", [
    ("2026-01-01T00:00:00Z", "2099-01-01T00:00:00"),                # zona en desde y no en hasta (el 500 original)
    ("2026-01-01T00:00:00", "2099-01-01T00:00:00Z"),                # al revés
    ("2026-01-01T00:00:00", "2099-01-01T00:00:00"),                 # ninguna
    ("2026-01-01T00:00:00+05:00", "2099-01-01T00:00:00-03:00"),     # zonas distintas
    ("2026-01-01T00:00:00Z", "2099-01-01T00:00:00+00:00"),
])
def test_combinaciones_de_zona_no_dan_500(api, desde, hasta):
    r = api.get("/api/auditoria", params={"desde": desde, "hasta": hasta}, headers=KEY)
    assert r.status_code == 200 and r.json()["total"] == 1


@pytest.mark.parametrize("desde,hasta", [
    ("2026-12-02T00:00:00Z", "2026-12-01T00:00:00"),                # hasta anterior a desde, con zona mezclada
    ("2026-12-02T00:00:00", "2026-12-01T00:00:00Z"),
    ("2026-12-01T00:00:00Z", "2026-12-01T00:00:00"),                # iguales una vez normalizadas: hasta <= desde
    ("2026-12-01T05:00:00+05:00", "2026-12-01T00:00:00Z"),          # el mismo instante expresado con otra zona
])
def test_rango_invertido_con_zonas_mezcladas_da_422_con_detail_de_texto(api, desde, hasta):
    r = api.get("/api/auditoria", params={"desde": desde, "hasta": hasta}, headers=KEY)
    assert r.status_code == 422 and r.json() == {"detail": c.MSG_AUDIT_RANGE_ORDER}


# ------------------------------------------------------------------ semántica: sin zona = UTC; con zona se convierte
def test_sin_zona_se_interpreta_como_utc(api):
    t = _momento(api)
    assert _total(api, desde=(t - timedelta(minutes=1)).replace(tzinfo=None).isoformat()) == 1
    assert _total(api, desde=(t + timedelta(minutes=1)).replace(tzinfo=None).isoformat()) == 0
    assert _total(api, hasta=(t + timedelta(minutes=1)).replace(tzinfo=None).isoformat()) == 1
    assert _total(api, hasta=(t - timedelta(minutes=1)).replace(tzinfo=None).isoformat()) == 0


def test_con_zona_se_convierte_a_utc_antes_de_consultar(api):
    t = _momento(api)
    cinco = timezone(timedelta(hours=5))
    # el mismo instante «hace un minuto» escrito en UTC+5: si no se convirtiera, la hora local (5 h por delante) lo dejaría fuera
    assert _total(api, desde=(t - timedelta(minutes=1)).astimezone(cinco).isoformat()) == 1
    assert _total(api, desde=(t + timedelta(minutes=1)).astimezone(cinco).isoformat()) == 0
    assert _total(api, hasta=(t + timedelta(minutes=1)).astimezone(cinco).isoformat()) == 1
    assert _total(api, hasta=(t - timedelta(minutes=1)).astimezone(cinco).isoformat()) == 0


def test_la_respuesta_sigue_siendo_json_en_todos_los_casos(api):
    for params in ({"desde": "2026-01-01T00:00:00Z", "hasta": "2026-06-01T00:00:00"}, {"desde": "2026-06-01T00:00:00Z", "hasta": "2026-01-01T00:00:00"}):
        r = api.get("/api/auditoria", params=params, headers=KEY)
        assert r.headers["content-type"].startswith("application/json")


# ------------------------------------------------------------------ también la función reutilizable
def test_consultar_acepta_fechas_con_y_sin_zona(api):
    t = _momento(api)
    items, total = au.consultar(desde=(t - timedelta(minutes=1)).replace(tzinfo=None), hasta=t + timedelta(minutes=1))
    assert total == 1 and len(items) == 1
    assert au.consultar(desde=(t + timedelta(minutes=1)).replace(tzinfo=None), hasta=t + timedelta(minutes=2))[1] == 0

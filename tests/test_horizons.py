"""GET /api/horizons: Horizons EN VIVO para coordenadas propias, con Horizons simulado (sin red)."""
import os
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config as c
from app.core.errors import register_handlers
from app.routers import horizons as router
from app.routers import lectura as lectura_router
from app.services import horizons as service
from app.services import lectura
from tests.horizons_falso import DATA, FakeHorizons

pytestmark = pytest.mark.skipif(not (DATA / "sites.csv").is_file(), reason="no hay data/sites.csv")

SHACKLETON = {"lat": -89.66, "lon": 129.2}
RANGE = {"desde": "2026-12-01T00:00Z", "hasta": "2026-12-15T00:00Z"}
OK = {**SHACKLETON, **RANGE}


@pytest.fixture
def fake(monkeypatch) -> FakeHorizons:
    f = FakeHorizons()
    monkeypatch.setattr(c, "HORIZONS_RETRY_BACKOFF_S", 0)
    service.set_transport(f.transport)
    service.reset_state()
    yield f
    service.set_transport(None)
    service.reset_state()


@pytest.fixture
def api(fake, tmp_path, monkeypatch) -> TestClient:
    for f in DATA.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    lectura.clear_cache()
    app = FastAPI()
    app.include_router(router.router)
    app.include_router(lectura_router.router)
    register_handlers(app)
    return TestClient(app)


def get(api, **params):
    return api.get("/api/horizons", params=params)


# ------------------------------------------------------------------ mismo contrato que los datos del catálogo
def test_las_filas_son_las_de_efemerides(api):
    live = get(api, **OK).json()
    ref = api.get("/api/efemerides/shackleton", params=RANGE).json()
    assert live["filas"] == ref["filas"]
    assert (live["inicio_utc"], live["fin_utc"], live["paso_horas"]) == (ref["inicio_utc"], ref["fin_utc"], ref["paso_horas"])
    assert live["site_id"] == c.CUSTOM_SITE_ID


def test_las_metricas_coinciden_con_las_del_catalogo(api):
    live = get(api, **OK).json()["metricas"]
    ref = api.get("/api/metricas", params={"site_id": "shackleton", **RANGE}).json()["resultados"][0]
    for key in ("horas_sol", "pct_sol", "horas_tierra_visible", "pct_tierra", "sol_el_max_deg", "sol_el_media_deg", "semaforo", "avisos"):
        assert live[key] == ref[key], key
    assert live["origen"] == c.ORIGEN_HORIZONS and live["referencia"] is None


def test_las_puestas_son_las_de_las_mismas_filas_y_solo_miran_dentro_del_rango(api):
    """Mismo cálculo que `/api/puestas`, pero solo con los datos del rango pedido (no ve más allá de `hasta`)."""
    live = get(api, **OK).json()["puestas"]
    rows = [r for r in lectura.ephemeris("shackleton") if lectura.parse_instant("desde", RANGE["desde"]) <= r["utc"]
            <= lectura.parse_instant("hasta", RANGE["hasta"])]
    assert live == lectura.sunsets_from_rows(c.CUSTOM_SITE_ID, rows, lectura.parse_instant("desde", RANGE["desde"]))
    full = api.get("/api/puestas/shackleton", params={"desde": RANGE["desde"]}).json()
    assert live["tierra"]["proxima_salida_utc"] == full["tierra"]["proxima_salida_utc"]
    assert live["tierra"]["horas_restantes"] == full["tierra"]["horas_restantes"]


def test_campos_propios_y_pide_a_horizons_lo_correcto(api, fake):
    body = get(api, **OK).json()
    assert body["fuente"] == c.HORIZONS_SOURCE and body["en_cache"] is False
    assert (body["lat_deg"], body["lon_este_deg"]) == (-89.66, 129.2) and body["consultado_utc"].endswith("Z")
    assert len(fake.requests) == 2
    sol, tierra = fake.requests
    assert (sol["COMMAND"], tierra["COMMAND"]) == ("'10'", "'399'")
    assert sol["CENTER"] == "'coord@301'" and sol["COORD_TYPE"] == "'GEODETIC'" and sol["QUANTITIES"] == "'4'"
    assert sol["SITE_COORD"] == "'129.2,-89.66,0'"


# ------------------------------------------------------------------ caché
def test_segunda_consulta_sale_de_cache(api, fake):
    first = get(api, **OK).json()
    second = get(api, **OK).json()
    assert first["en_cache"] is False and second["en_cache"] is True
    assert second["filas"] == first["filas"] and len(fake.requests) == 2


def test_coordenadas_casi_iguales_comparten_cache(api, fake):
    get(api, **OK)
    again = get(api, **{**OK, "lat": -89.660001, "lon": 129.200002}).json()
    assert again["en_cache"] is True and len(fake.requests) == 2


def test_otro_rango_o_paso_no_comparte_cache(api, fake):
    get(api, **OK)
    get(api, **{**OK, "paso_horas": 2})
    assert len(fake.requests) == 4


# ------------------------------------------------------------------ validación
@pytest.mark.parametrize("cambio", [
    {"lat": 91}, {"lat": -90.5}, {"lon": 181}, {"lon": -180.1}, {"lat": "nan"}, {"lon": "inf"}, {"lat": "abc"},
    {"paso_horas": 0}, {"paso_horas": 25}, {"paso_horas": 1.5}, {"paso_horas": "x"},
    {"hasta": "2027-01-10T00:00Z"},                         # > 35 días
    {"hasta": "2026-12-01T00:00Z"}, {"hasta": "2026-11-30T00:00Z"},   # hasta <= desde
    {"desde": "ayer"}, {"hasta": "2026-13-45T00:00Z"},
    {"desde": "1800-01-01T00:00Z", "hasta": "1800-01-02T00:00Z"}, {"desde": "2200-01-01T00:00Z", "hasta": "2200-01-02T00:00Z"},
])
def test_validacion_422_sin_llamar_a_horizons(api, fake, cambio):
    response = get(api, **{**OK, **cambio})
    assert response.status_code == 422 and "detail" in response.json()
    assert fake.requests == []


@pytest.mark.parametrize("faltante", ["lat", "lon", "desde", "hasta"])
def test_parametros_obligatorios(api, fake, faltante):
    params = {k: v for k, v in OK.items() if k != faltante}
    assert get(api, **params).status_code == 422 and fake.requests == []


def test_limites_validos_se_aceptan(api):
    assert get(api, lat=90, lon=-180, desde=RANGE["desde"], hasta="2026-12-02T00:00Z", paso_horas=24).status_code == 200
    assert get(api, lat=-90, lon=180, desde=RANGE["desde"], hasta="2027-01-05T00:00Z").status_code == 200   # 35 días exactos


# ------------------------------------------------------------------ fallos de JPL
@pytest.mark.parametrize("modo, estado", [("http500", 502), ("timeout", 504), ("sin_soe", 502), ("otro_centro", 502), ("desfase", 502)])
def test_fallos_de_jpl(api, fake, modo, estado):
    fake.mode = modo
    response = get(api, **OK)
    assert response.status_code == estado
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail and "SECRETO-INTERNO-JPL" not in response.text


def test_reintenta_y_se_recupera(api, fake):
    fake.fail_first = 1
    assert get(api, **OK).status_code == 200
    assert len(fake.requests) == 3                          # 1 fallo + reintento + Tierra


def test_agota_los_reintentos(api, fake):
    fake.mode = "http500"
    get(api, **OK)
    assert len(fake.requests) == c.HORIZONS_RETRIES + 1


def test_un_fallo_no_queda_en_cache(api, fake):
    fake.mode = "timeout"
    assert get(api, **OK).status_code == 504
    fake.mode = "ok"
    assert get(api, **OK).json()["en_cache"] is False


# ------------------------------------------------------------------ límite de consultas
def test_limite_por_cliente_devuelve_429_con_retry_after(api, monkeypatch):
    monkeypatch.setattr(service, "_client_limiter", service.SlidingWindowLimiter(2, 60.0))
    for i in range(2):
        assert get(api, **{**OK, "lat": -80 - i}).status_code == 200
    blocked = get(api, **{**OK, "lat": -70})
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
    assert "detail" in blocked.json()


def test_limite_global_devuelve_429(api, monkeypatch):
    monkeypatch.setattr(service, "_global_limiter", service.SlidingWindowLimiter(1, 60.0))
    assert get(api, **OK).status_code == 200
    assert get(api, **{**OK, "lat": -70}).status_code == 429


def test_la_cache_no_gasta_cupo(api, monkeypatch):
    monkeypatch.setattr(service, "_client_limiter", service.SlidingWindowLimiter(1, 60.0))
    assert get(api, **OK).status_code == 200
    assert all(get(api, **OK).status_code == 200 for _ in range(3))


def test_el_limite_no_se_gasta_si_la_validacion_falla(api, monkeypatch):
    monkeypatch.setattr(service, "_client_limiter", service.SlidingWindowLimiter(1, 60.0))
    assert get(api, **{**OK, "lat": 99}).status_code == 422
    assert get(api, **OK).status_code == 200


# ------------------------------------------------------------------ red real (opcional)
@pytest.mark.red
@pytest.mark.skipif(not os.environ.get("HORIZONS_RED"), reason="define HORIZONS_RED=1 para consultar JPL de verdad")
def test_red_horizons_reproduce_el_csv(tmp_path):
    service.set_transport(None)
    service.reset_state()
    live = service.query(-89.66, 129.2, "2026-12-01T00:00Z", "2026-12-02T00:00Z", 1)
    reference = FakeHorizons().rows[:25]
    for got, want in zip(live["filas"], reference):
        assert got["utc"] == want["utc"]
        assert abs(got["sol_el_deg"] - float(want["sol_el_deg"])) < 1e-3

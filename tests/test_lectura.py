"""Endpoints de lectura (/api/efemerides, /api/metricas, /api/puestas) con los CSV reales de data/."""
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import lectura
from app.services import lectura as service

REAL_DATA = Path(__file__).resolve().parents[1] / "data"
SITES = ["apollo11", "apollo15", "apollo17", "shackleton", "im1_odysseus", "blue_ghost_m1",
         "chandrayaan3", "change4", "change6", "slim"]
FULL = {"desde": "2026-12-01T00:00Z", "hasta": "2027-01-01T00:00Z"}

pytestmark = pytest.mark.skipif(not (REAL_DATA / "sites.csv").is_file(), reason="no hay data/sites.csv")


@pytest.fixture
def api(tmp_path, monkeypatch) -> TestClient:
    for f in REAL_DATA.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    service.clear_cache()
    app = FastAPI()
    app.include_router(lectura.router)
    return TestClient(app)


def metrics(api, *sites, **params):
    return api.get("/api/metricas", params={"site_id": list(sites), **params})


# ------------------------------------------------------------------ métricas
def test_shackleton_sol_continuo_y_tierra_parcial(api):
    body = metrics(api, "shackleton", **FULL).json()
    item = body["resultados"][0]
    assert item["pct_sol"] == 100.0 and item["horas_sol"] == 744
    assert item["pct_tierra"] == 39.11 and item["horas_tierra_visible"] == 291
    assert item["semaforo"] == {"energia": "verde", "comunicacion": "rojo", "global": "rojo"}
    assert item["origen"] == "calculado"
    assert [a["codigo"] for a in item["avisos"]] == ["sensible_al_relieve"]       # el Sol polar nunca pasa de unos 1,5°


def test_apollo11_con_sol_alto_no_tiene_aviso_de_relieve(api):
    item = metrics(api, "apollo11", **FULL).json()["resultados"][0]
    assert item["sol_el_max_deg"] > 3 and item["avisos"] == []


@pytest.mark.parametrize("site_id", ["change4", "change6"])
def test_cara_oculta_sin_tierra(api, site_id):
    item = metrics(api, site_id, **FULL).json()["resultados"][0]
    assert item["horas_tierra_visible"] == 0 and item["pct_tierra"] == 0
    assert item["semaforo"]["comunicacion"] == "rojo"


@pytest.mark.parametrize("site_id", SITES)
def test_recalculo_coincide_con_metricas_csv(api, site_id):
    ref = metrics(api, site_id, **FULL).json()["resultados"][0]["referencia"]
    assert ref is not None and ref["coincide"] is True


def test_respuesta_siempre_es_lista_de_resultados(api):
    one = metrics(api, "apollo11", **FULL).json()
    two = metrics(api, "apollo11", "slim", **FULL).json()
    assert list(one) == ["resultados"] and len(one["resultados"]) == 1
    assert [r["site_id"] for r in two["resultados"]] == ["apollo11", "slim"]


def test_sitios_repetidos_se_calculan_una_vez(api):
    assert len(metrics(api, "slim", "slim", **FULL).json()["resultados"]) == 1


def test_rango_por_defecto_es_todo_el_archivo(api):
    item = metrics(api, "apollo11").json()["resultados"][0]
    assert (item["inicio_utc"], item["fin_utc"]) == (FULL["desde"], FULL["hasta"])


def test_subrango_sin_referencia(api):
    item = metrics(api, "apollo11", desde="2026-12-01T00:00Z", hasta="2026-12-02T00:00Z").json()["resultados"][0]
    assert item["referencia"] is None
    assert 0 <= item["horas_sol"] <= 24


@pytest.mark.parametrize("sites,params,status", [
    ((), {}, 422),                                                    # falta site_id
    (("apollo11", "apollo15", "apollo17", "slim"), {}, 422),          # más de 3 sitios
    (("no_existe",), {}, 404),
    (("apollo11",), {"desde": "2026-12-01T00:00Z", "hasta": "2026-11-30T00:00Z"}, 422),
    (("apollo11",), {"desde": "ayer"}, 422),
    (("apollo11",), {"desde": "2026-12-01T00:00Z", "hasta": "2027-02-01T00:00Z"}, 422),
])
def test_errores_de_metricas(api, sites, params, status):
    response = metrics(api, *sites, **params)
    assert response.status_code == status
    assert isinstance(response.json()["detail"], (str, list))


# ------------------------------------------------------------------ efemérides
def test_efemerides_rango_por_defecto_limita_a_35_dias(api):
    body = api.get("/api/efemerides/apollo11").json()
    assert body["paso_horas"] == 1 and len(body["filas"]) == 745      # el archivo solo tiene 31 días
    assert body["filas"][0] == {"utc": "2026-12-01T00:00Z", "sol_az_deg": body["filas"][0]["sol_az_deg"],
                                "sol_el_deg": body["filas"][0]["sol_el_deg"],
                                "tierra_az_deg": body["filas"][0]["tierra_az_deg"],
                                "tierra_el_deg": body["filas"][0]["tierra_el_deg"]}


def test_efemerides_rango_explicito_incluye_extremos(api):
    body = api.get("/api/efemerides/shackleton", params={"desde": "2026-12-05T06:00Z", "hasta": "2026-12-05T09:00Z"}).json()
    assert [r["utc"] for r in body["filas"]] == ["2026-12-05T06:00Z", "2026-12-05T07:00Z",
                                                  "2026-12-05T08:00Z", "2026-12-05T09:00Z"]


@pytest.mark.parametrize("params", [
    {"desde": "2026-12-01T00:00Z", "hasta": "2027-01-15T00:00Z"},     # fuera de los datos
    {"desde": "2026-11-01T00:00Z"},                                   # antes de los datos
    {"desde": "2026-12-02T00:00Z", "hasta": "2026-12-02T00:00Z"},     # rango vacío
    {"desde": "2026-12-01 00:00"},                                    # formato
])
def test_efemerides_422(api, params):
    assert api.get("/api/efemerides/apollo11", params=params).status_code == 422


def test_efemerides_mas_de_35_dias_es_422(api, tmp_path):
    start = datetime(2026, 12, 1)
    rows = ["utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg"]
    rows += [f"{(start + timedelta(hours=6 * i)):%Y-%m-%dT%H:%MZ},10,1,200,5" for i in range(40 * 4 + 1)]   # 40 días
    (tmp_path / "efemerides_slim.csv").write_text("\n".join(rows) + "\n")
    body = api.get("/api/efemerides/slim", params={"desde": "2026-12-01T00:00Z", "hasta": "2027-01-10T00:00Z"})
    assert body.status_code == 422 and "35" in body.json()["detail"]


def test_efemerides_sitio_inexistente_404(api):
    response = api.get("/api/efemerides/no_existe")
    assert response.status_code == 404 and "detail" in response.json()


def test_efemerides_sitio_sin_archivo_404(api, tmp_path):
    (tmp_path / "efemerides_slim.csv").unlink()
    assert api.get("/api/efemerides/slim").status_code == 404


def test_efemerides_invalidas_503(api, tmp_path):
    (tmp_path / "efemerides_slim.csv").write_text("utc,sol_az_deg\n2026-12-01T00:00Z,1\n")
    assert api.get("/api/efemerides/slim").status_code == 503


def test_sin_sites_csv_503(api, tmp_path):
    (tmp_path / "sites.csv").unlink()
    service.clear_cache()
    assert api.get("/api/efemerides/slim").status_code == 503


# ------------------------------------------------------------------ puestas
def test_puesta_del_sol_apolo15(api):
    body = api.get("/api/puestas/apollo15", params={"desde": "2026-12-01T00:00Z"}).json()
    assert body["sol"]["visible"] is True
    assert body["sol"]["proxima_puesta_utc"] == "2026-12-01T12:00Z" and body["sol"]["horas_restantes"] == 12.0
    assert body["tierra"] == {"visible": True, "proxima_puesta_utc": None, "proxima_salida_utc": None,
                              "horas_restantes": None}


def test_puestas_sol_polar_sin_cambios(api):
    body = api.get("/api/puestas/shackleton", params={"desde": "2026-12-05T06:00Z"}).json()
    assert body["sol"] == {"visible": True, "proxima_puesta_utc": None, "proxima_salida_utc": None,
                           "horas_restantes": None}
    assert body["tierra"]["horas_restantes"] is not None


def test_puestas_tierra_nunca_visible_en_cara_oculta(api):
    tierra = api.get("/api/puestas/change4", params={"desde": "2026-12-10T00:00Z"}).json()["tierra"]
    assert tierra["visible"] is False and tierra["proxima_salida_utc"] is None


def test_puestas_instante_intermedio_usa_la_fila_anterior(api):
    a = api.get("/api/puestas/apollo15", params={"desde": "2026-12-01T00:30Z"}).json()
    assert a["sol"]["horas_restantes"] == 11.5


@pytest.mark.parametrize("site_id,desde,status", [
    ("apollo15", "2027-02-01T00:00Z", 422),
    ("apollo15", "mañana", 422),
    ("no_existe", "2026-12-01T00:00Z", 404),
])
def test_puestas_errores(api, site_id, desde, status):
    assert api.get(f"/api/puestas/{site_id}", params={"desde": desde}).status_code == status


# ------------------------------------------------------------------ caché
def test_cache_se_invalida_al_cambiar_el_archivo(api, tmp_path):
    first = api.get("/api/efemerides/slim").json()
    again = api.get("/api/efemerides/slim").json()
    assert first == again
    lines = (tmp_path / "efemerides_slim.csv").read_text().splitlines()
    (tmp_path / "efemerides_slim.csv").write_text("\n".join(lines[:100]) + "\n")   # menos filas: cambia el tamaño
    assert len(api.get("/api/efemerides/slim").json()["filas"]) == 99

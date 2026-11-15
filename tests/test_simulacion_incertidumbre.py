"""Incertidumbre del azimut por punto (escalada por 1/cos de la elevación) y avisos por punto de la simulación."""
import json
import math
import shutil
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis import simulacion, validation
from app.core import config as c
from app.core.errors import register_handlers
from app.routers import simulacion as simulacion_router
from app.services import lectura

DATA = Path(__file__).resolve().parents[1] / "data"
PARQUET = DATA.parent / "scripts/datos/validacion/horizons_verdad.parquet"
SITES = ("slim", "apollo11", "apollo15", "apollo17", "blue_ghost_m1", "shackleton")
YEAR = {"desde": "2027-06-02T00:00Z", "hasta": "2028-06-01T00:00Z", "paso_horas": 6}


@pytest.fixture
def api(tmp_path, monkeypatch) -> TestClient:
    for name in ("sites.csv", *(f"efemerides_{s}.csv" for s in SITES)):
        shutil.copy(DATA / name, tmp_path / name)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    lectura.clear_cache()
    app = FastAPI()
    app.include_router(simulacion_router.router)
    register_handlers(app)
    return TestClient(app)


def get(api, site, **params):
    return api.get(f"/api/simulacion/{site}", params=params).json()


# ------------------------------------------------------------------ fórmula
def _table(direction=1.0, el_rmse=0.5):
    block = {c.KEY_RMSE_DEG: direction, c.KEY_RMSE_MONTHS: [direction] * 12}
    var = {c.KEY_RMSE_DEG: el_rmse, c.KEY_RMSE_MONTHS: [el_rmse] * 12}
    return {**{col: var for col in simulacion.VARIABLES}, c.KEY_ANGULAR: {c.KEY_SOL: block, c.KEY_TIERRA: block}}


@pytest.mark.parametrize("elevation,expected", [(0.0, 1.0), (60.0, 2.0), (-60.0, 2.0), (89.9999, 180.0), (-90.0, 180.0)])
def test_el_azimut_escala_por_uno_sobre_cos_de_la_elevacion_con_tope(elevation, expected):
    series = {col: np.array([elevation if "el" in col else 10.0]) for col in simulacion.VARIABLES}
    out = simulacion._uncertainty(_table(), np.array([0]), series)[0]
    assert out[c.COL_SOL_AZ] == pytest.approx(expected, rel=1e-3) and out[c.COL_TIERRA_AZ] == pytest.approx(expected, rel=1e-3)
    assert out[c.COL_SOL_EL] == out[c.COL_TIERRA_EL] == 0.5          # las elevaciones no se escalan


def test_cada_cuerpo_usa_su_propio_error_de_direccion_y_su_propia_elevacion():
    table = _table()
    table[c.KEY_ANGULAR][c.KEY_TIERRA] = {c.KEY_RMSE_DEG: 0.2, c.KEY_RMSE_MONTHS: [0.2] * 12}
    series = {c.COL_SOL_AZ: np.array([0.0]), c.COL_SOL_EL: np.array([0.0]), c.COL_TIERRA_AZ: np.array([0.0]),
              c.COL_TIERRA_EL: np.array([60.0])}
    out = simulacion._uncertainty(table, np.array([0]), series)[0]
    assert out[c.COL_SOL_AZ] == pytest.approx(1.0) and out[c.COL_TIERRA_AZ] == pytest.approx(0.4)


def test_un_mes_sin_dato_usa_el_total():
    table = _table()
    table[c.KEY_ANGULAR][c.KEY_SOL][c.KEY_RMSE_MONTHS][3] = None
    table[c.KEY_ANGULAR][c.KEY_SOL][c.KEY_RMSE_DEG] = 1.5
    series = {col: np.array([0.0]) for col in simulacion.VARIABLES}
    assert simulacion._uncertainty(table, np.array([3]), series)[0][c.COL_SOL_AZ] == 1.5


def test_sin_tabla_no_hay_incertidumbre():
    assert simulacion._uncertainty(None, np.array([0, 1]), {}) == [None, None]


# ------------------------------------------------------------------ endpoint
def test_cerca_del_nadir_la_incertidumbre_del_azimut_se_dispara(api):
    body = get(api, "apollo11", desde="2027-08-30T00:00Z", hasta="2027-08-30T06:00Z", paso_horas=6)
    near, farther = body["puntos"]
    assert near["sol_el_deg"] == pytest.approx(-89.3, abs=0.1) and near["azimut_solar_fiable"] is False
    assert near["incertidumbre_deg"]["sol_az_deg"] > 20                    # antes: 6,8° para un error real de 127°
    low = get(api, "apollo11", desde="2027-06-10T00:00Z", hasta="2027-06-10T06:00Z", paso_horas=6)["puntos"][0]
    assert abs(low["sol_el_deg"]) < 20 and low["azimut_solar_fiable"] is True
    assert low["incertidumbre_deg"]["sol_az_deg"] < 3 and near["incertidumbre_deg"]["sol_az_deg"] > 10 * low["incertidumbre_deg"]["sol_az_deg"]


def test_la_incertidumbre_nunca_supera_media_vuelta_ni_es_negativa(api):
    for site in ("apollo11", "slim"):
        for p in get(api, site, **YEAR)["puntos"]:
            assert all(0 < v <= 180 for v in p["incertidumbre_deg"].values())


@pytest.mark.parametrize("site,flagged", [("slim", True), ("blue_ghost_m1", True), ("apollo17", True), ("apollo11", True),
                                          ("apollo15", False), ("shackleton", False)])
def test_el_aviso_de_cenit_sale_cuando_algun_punto_pedido_pasa_de_70_grados(api, site, flagged):
    body = get(api, site, **YEAR)
    codes = [a["codigo"] for a in body["avisos"]]
    bad = [p for p in body["puntos"] if not p["azimut_solar_fiable"]]
    assert (c.AVISO_AZIMUT_CENIT in codes) is flagged and bool(bad) is flagged
    assert all(abs(p["sol_el_deg"]) > c.SIM_AZIMUTH_WARN_EL_DEG and p["origen"] == c.ORIGEN_PROYECCION for p in bad)
    good = [p for p in body["puntos"] if p["azimut_solar_fiable"]]
    assert all(abs(p["sol_el_deg"]) <= c.SIM_AZIMUTH_WARN_EL_DEG for p in good if p["origen"] == c.ORIGEN_PROYECCION)
    if flagged:
        message = next(a["mensaje"] for a in body["avisos"] if a["codigo"] == c.AVISO_AZIMUT_CENIT)
        assert f"En {len(bad)} de {len(body['puntos'])} puntos proyectados" in message


def test_el_aviso_depende_de_los_puntos_pedidos_no_del_sitio(api):
    """Antes salía siempre en Apolo 11 aunque el Sol estuviera en el horizonte (DEF-sim-2 del QA)."""
    body = get(api, "apollo11", desde="2027-06-10T00:00Z", hasta="2027-06-11T00:00Z", paso_horas=24)
    assert [a["codigo"] for a in body["avisos"]] == [c.AVISO_PROYECCION]
    assert all(p["azimut_solar_fiable"] for p in body["puntos"])


def test_los_datos_reales_siempre_son_fiables_y_sin_incertidumbre(api):
    body = get(api, "apollo11", desde="2026-12-01T00:00Z", hasta="2026-12-10T00:00Z", paso_horas=6)
    assert all(p["azimut_solar_fiable"] and p["incertidumbre_deg"] is None for p in body["puntos"]) and body["avisos"] == []


def test_la_tabla_sin_bloque_angular_da_503(api, monkeypatch, tmp_path):
    table = json.loads(c.ERRORS_PATH.read_text(encoding="utf-8"))
    for site in table[c.KEY_SITES].values():
        site.pop(c.KEY_ANGULAR)
    path = tmp_path / "errores.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(c, "ERRORS_PATH", path)
    lectura.clear_cache()
    response = api.get("/api/simulacion/apollo15", params={"desde": "2027-06-02T00:00Z", "hasta": "2027-06-03T00:00Z"})
    assert response.status_code == 503 and response.json() == {"detail": c.MSG_SIM_ERRORS_INVALID}


# ------------------------------------------------------------------ calibración contra la verdad de Horizons
def _coverage(api, site, column):
    truth = pl.read_parquet(PARQUET).filter(pl.col("site_id") == site).filter(pl.col("utc").dt.hour().is_in([0, 6, 12, 18]))
    points = {p["utc"]: p for p in get(api, site, **YEAR)["puntos"]}
    ratios, near_zenith = [], []
    for row in truth.iter_rows(named=True):
        point = points.get(row["utc"].strftime(c.UTC_FORMAT))
        if point is None or point["origen"] != c.ORIGEN_PROYECCION:
            continue
        error = abs((point[column] - row[column] + 180) % 360 - 180)
        ratio = error / point["incertidumbre_deg"][column]
        ratios.append(ratio)
        if column == c.COL_SOL_AZ and abs(point["sol_el_deg"]) > 85:
            near_zenith.append(ratio)
    return np.array(ratios), np.array(near_zenith)


@pytest.mark.parametrize("site", ["apollo11", "slim", "apollo17", "shackleton"])
@pytest.mark.parametrize("column", [c.COL_SOL_AZ, c.COL_TIERRA_AZ])
def test_dos_veces_la_incertidumbre_cubre_casi_todos_los_azimuts(api, site, column):
    ratios, _ = _coverage(api, site, column)
    assert len(ratios) > 1400
    assert float((ratios <= 2).mean()) >= 0.97
    assert float((ratios <= 1).mean()) >= 0.55          # el 1× ya no es el 43–77 % de antes


def test_cerca_del_cenit_de_apolo_11_la_cifra_ya_no_es_enganosa(api):
    ratios, near_zenith = _coverage(api, "apollo11", c.COL_SOL_AZ)
    assert len(near_zenith) >= 50
    assert float((near_zenith <= 3).mean()) >= 0.85       # antes: 18 % dentro de 3× en 88–90° y 85,7 % en 85–88°
    assert float(np.max(ratios)) < 12                     # antes: 18,7


# ------------------------------------------------------------------ la validación guarda el error de dirección
def test_la_tabla_de_la_api_trae_el_error_angular_de_cada_sitio():
    table = json.loads(c.ERRORS_PATH.read_text(encoding="utf-8"))[c.KEY_SITES]
    assert len(table) == 10
    for site in table.values():
        for body in (c.KEY_SOL, c.KEY_TIERRA):
            months = site[c.KEY_ANGULAR][body][c.KEY_RMSE_MONTHS]
            assert len(months) == 12 and all(m is not None and m > 0 for m in months) and site[c.KEY_ANGULAR][body][c.KEY_RMSE_DEG] > 0


def test_el_error_angular_no_se_dispara_cerca_del_cenit_como_el_azimut():
    table = json.loads(c.ERRORS_PATH.read_text(encoding="utf-8"))[c.KEY_SITES]["apollo11"]
    assert table[c.COL_SOL_AZ][c.KEY_RMSE_DEG] > 4 * table[c.KEY_ANGULAR][c.KEY_SOL][c.KEY_RMSE_DEG]


def test_la_tabla_angular_esta_al_dia():
    from app.analysis import frames
    table = json.loads(c.ERRORS_PATH.read_text(encoding="utf-8"))[c.KEY_SITES]["slim"][c.KEY_ANGULAR]
    fresh = validation.evaluate_site(frames.from_rows(lectura.ephemeris("slim")), frames.from_parquet(PARQUET, "slim"))
    for body in (c.KEY_SOL, c.KEY_TIERRA):
        assert table[body][c.KEY_RMSE_DEG] == pytest.approx(fresh[c.KEY_ANGULAR][body][c.KEY_RMSE_DEG], abs=1e-3)

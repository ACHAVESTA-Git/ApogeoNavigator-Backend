"""GET /api/simulacion/{site_id}: datos reales dentro del rango y proyección con incertidumbre fuera."""
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config as c
from app.routers import simulacion
from app.services import lectura

DATA = Path(__file__).resolve().parents[1] / "data"
SITES = ("slim", "apollo11", "apollo15", "shackleton")
LAST_DATA = "2027-06-01T00:00Z"


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    for name in ("sites.csv", *(f"efemerides_{s}.csv" for s in SITES)):
        shutil.copy(DATA / name, tmp_path / name)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    lectura.clear_cache()
    return tmp_path


@pytest.fixture
def api(data_dir) -> TestClient:
    app = FastAPI()
    app.include_router(simulacion.router)
    return TestClient(app)


def get(api, site="slim", **params):
    return api.get(f"/api/simulacion/{site}", params=params)


def test_por_defecto_son_30_dias_de_datos_reales(api):
    body = get(api).json()
    assert body["desde_utc"] == "2026-12-01T00:00Z" and body["hasta_utc"] == "2026-12-31T00:00Z"
    assert body["fin_datos_utc"] == LAST_DATA and body["paso_horas"] == 1.0
    assert len(body["puntos"]) == 30 * 24 + 1 and body["avisos"] == []
    assert {p["origen"] for p in body["puntos"]} == {"datos"} and all(p["incertidumbre_deg"] is None for p in body["puntos"])


def test_los_puntos_de_datos_son_las_efemerides(api):
    point = get(api, desde="2026-12-05T06:00Z", hasta="2026-12-05T07:00Z").json()["puntos"][0]
    row = next(r for r in lectura.ephemeris("slim") if lectura.fmt(r[c.COL_UTC]) == "2026-12-05T06:00Z")
    for column in (c.COL_SOL_AZ, c.COL_SOL_EL, c.COL_TIERRA_AZ, c.COL_TIERRA_EL):
        assert point[column] == pytest.approx(row[column], abs=1e-3)


def test_el_rango_que_cruza_el_final_mezcla_datos_y_proyeccion(api):
    body = get(api, desde="2027-05-30T00:00Z", hasta="2027-06-03T00:00Z", paso_horas=6).json()
    origins = [p["origen"] for p in body["puntos"]]
    assert origins == ["datos"] * 9 + ["proyeccion"] * 8                  # el último dato (01/06 00:00) aún es real
    projected = next(p for p in body["puntos"] if p["origen"] == "proyeccion")
    assert set(projected["incertidumbre_deg"]) == {"sol_az_deg", "sol_el_deg", "tierra_az_deg", "tierra_el_deg"}
    assert all(v > 0 for v in projected["incertidumbre_deg"].values())
    assert [a["codigo"] for a in body["avisos"]] == [c.AVISO_PROYECCION]


def test_la_incertidumbre_crece_con_el_horizonte(api):
    points = get(api, desde="2027-06-02T00:00Z", hasta="2028-05-31T00:00Z", paso_horas=24).json()["puntos"]
    first, last = points[0]["incertidumbre_deg"], points[-1]["incertidumbre_deg"]
    assert last["sol_el_deg"] > first["sol_el_deg"]


def test_proyeccion_a_doce_meses_cerca_de_la_verdad(api):
    import polars as pl
    truth = pl.read_parquet(DATA.parent / "scripts/datos/validacion/horizons_verdad.parquet").filter(
        (pl.col("site_id") == "slim") & (pl.col("utc").dt.hour() == 0) & (pl.col("utc").dt.day() == 15))
    points = {p["utc"]: p for p in get(api, desde="2027-06-02T00:00Z", hasta="2028-06-01T00:00Z", paso_horas=24).json()["puntos"]}
    checked = 0
    for row in truth.iter_rows(named=True):
        key = row["utc"].strftime(c.UTC_FORMAT)
        if key in points:
            p = points[key]
            assert abs(p[c.COL_TIERRA_EL] - row[c.COL_TIERRA_EL]) < 3 * p["incertidumbre_deg"][c.COL_TIERRA_EL] + 1
            assert abs(p[c.COL_SOL_EL] - row[c.COL_SOL_EL]) < 3 * p["incertidumbre_deg"][c.COL_SOL_EL] + 1
            checked += 1
    assert checked >= 10


def test_solo_proyeccion_si_el_rango_empieza_tras_los_datos(api):
    points = get(api, desde="2027-07-01T00:00Z", hasta="2027-07-02T00:00Z", paso_horas=12).json()["puntos"]
    assert [p["origen"] for p in points] == ["proyeccion"] * 3


def test_azimut_solar_no_fiable_cerca_del_cenit(api):
    codes = [a["codigo"] for a in get(api, "apollo11", desde="2027-06-02T00:00Z", hasta="2027-06-04T00:00Z").json()["avisos"]]
    assert codes == [c.AVISO_PROYECCION, c.AVISO_AZIMUT_CENIT]
    other = [a["codigo"] for a in get(api, "apollo15", desde="2027-06-02T00:00Z", hasta="2027-06-04T00:00Z").json()["avisos"]]
    assert other == [c.AVISO_PROYECCION]


def test_la_tabla_de_errores_viaja_con_el_codigo_no_con_los_datos(api, data_dir):
    assert c.ERRORS_PATH.is_file() and c.ERRORS_PATH.parent.name == "analysis"
    assert not (data_dir / c.ERRORS_FILENAME).exists()                    # data/ no la lleva y la incertidumbre sigue ahí
    points = get(api, desde="2027-06-02T00:00Z", hasta="2027-06-03T00:00Z").json()["puntos"]
    assert points[0]["origen"] == "proyeccion" and points[0]["incertidumbre_deg"] is not None


def _with_errors_file(monkeypatch, tmp_path, content: str | None):
    path = tmp_path / "errores.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(c, "ERRORS_PATH", path)
    lectura.clear_cache()


@pytest.mark.parametrize("content", [
    "{esto no es json", "", "[]", "null", '{"version": 1}', '{"version": 99, "sitios": {}}',
    '{"version": 1, "sitios": {"slim": {"sol_az_deg": {}}}}',
    '{"version": 1, "sitios": {"slim": []}}',
    '{"version": 1, "sitios": {"slim": {"sol_az_deg": {"rmse_deg": 1, "rmse_por_mes_deg": [1, 2]}}}}',
])
def test_tabla_de_errores_corrupta_da_503_con_detail(api, monkeypatch, tmp_path, content):
    _with_errors_file(monkeypatch, tmp_path, content)
    response = get(api, desde="2027-06-02T00:00Z", hasta="2027-06-03T00:00Z")
    assert response.status_code == 503 and response.json() == {"detail": c.MSG_SIM_ERRORS_INVALID}


def test_tabla_de_errores_ausente_da_503(api, monkeypatch, tmp_path):
    _with_errors_file(monkeypatch, tmp_path, None)
    response = get(api, desde="2027-06-02T00:00Z", hasta="2027-06-03T00:00Z")
    assert response.status_code == 503 and response.json() == {"detail": c.MSG_SIM_ERRORS_MISSING}


def test_la_tabla_de_errores_no_hace_falta_si_todo_es_dato_real(api, monkeypatch, tmp_path):
    _with_errors_file(monkeypatch, tmp_path, None)
    assert get(api, desde="2026-12-01T00:00Z", hasta="2026-12-02T00:00Z").status_code == 200


def test_un_sitio_sin_validacion_avisa_en_vez_de_callar(api, monkeypatch, tmp_path):
    import json
    table = json.loads(Path(c.ERRORS_PATH).read_text(encoding="utf-8"))
    del table[c.KEY_SITES]["slim"]
    path = tmp_path / "errores.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(c, "ERRORS_PATH", path)
    lectura.clear_cache()
    body = get(api, desde="2027-06-02T00:00Z", hasta="2027-06-03T00:00Z").json()
    assert body["puntos"][0]["incertidumbre_deg"] is None
    assert [a["codigo"] for a in body["avisos"]] == [c.AVISO_PROYECCION, c.AVISO_SIN_INCERTIDUMBRE]


def test_un_ano_a_paso_de_1_h_cabe_en_el_tope(api):
    response = get(api, desde="2027-06-01T00:00Z", hasta="2028-06-01T00:00Z")
    assert response.status_code == 200 and len(response.json()["puntos"]) == 8785 <= c.SIM_MAX_POINTS


def test_el_tope_de_puntos_da_un_error_claro(api):
    response = get(api, desde="2026-12-01T00:00Z", hasta="2028-06-01T00:00Z")
    assert response.status_code == 422
    assert f"el máximo es {c.SIM_MAX_POINTS}" in response.json()["detail"] and "paso_horas" in response.json()["detail"]


def test_paso_fino_interpola_entre_filas(api):
    points = get(api, desde="2026-12-05T06:00Z", hasta="2026-12-05T07:00Z", paso_horas=0.5).json()["puntos"]
    a, mid, b = points
    assert min(a[c.COL_SOL_EL], b[c.COL_SOL_EL]) - 1e-6 <= mid[c.COL_SOL_EL] <= max(a[c.COL_SOL_EL], b[c.COL_SOL_EL]) + 1e-6


@pytest.mark.parametrize("site,params,status", [
    ("no_existe", {}, 404),
    ("slim", {"paso_horas": 0.1}, 422),
    ("slim", {"paso_horas": 30}, 422),
    ("slim", {"desde": "2026-12-01T00:00Z", "hasta": "2027-06-01T00:00Z", "paso_horas": 0.25}, 422),   # demasiados puntos
    ("slim", {"desde": "2027-06-02T00:00Z", "hasta": "2028-07-01T00:00Z", "paso_horas": 24}, 422),     # más de 366 días
    ("slim", {"desde": "2026-11-01T00:00Z"}, 422),
    ("slim", {"desde": "2026-12-05T00:00Z", "hasta": "2026-12-05T00:00Z"}, 422),
    ("slim", {"desde": "ayer"}, 422),
    ("slim", {"paso_horas": "x"}, 422),
])
def test_errores(api, site, params, status):
    response = get(api, site, **params)
    assert response.status_code == status and "detail" in response.json()


def test_el_modelo_se_vuelve_a_ajustar_si_cambian_los_datos(api, data_dir):
    before = get(api, desde="2027-07-01T00:00Z", hasta="2027-07-01T06:00Z").json()["puntos"][0]
    lines = (data_dir / "efemerides_slim.csv").read_text().splitlines()
    (data_dir / "efemerides_slim.csv").write_text("\n".join(lines[: 24 * 100]) + "\n")      # solo ~100 días de datos
    after = get(api, desde="2027-03-20T00:00Z", hasta="2027-03-20T06:00Z")
    assert before["origen"] == "proyeccion" and after.status_code == 200
    assert after.json()["puntos"][0]["origen"] == "proyeccion" and after.json()["fin_datos_utc"] != LAST_DATA

"""Validación de la proyección con los datos reales y la verdad de terreno de Horizons (Parquet)."""
import json
from pathlib import Path

import polars as pl
import pytest

from app.analysis import frames, validation
from app.core import config as c
from app.services import lectura

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "scripts/datos/validacion/horizons_verdad.parquet"
DATA = ROOT / "data"
SITES = ["apollo11", "apollo15", "apollo17", "shackleton", "im1_odysseus", "blue_ghost_m1", "chandrayaan3",
         "change4", "change6", "slim"]
EXPECTED_ROWS = 366 * 24 + 1                     # 2027-06-01 a 2028-06-01, extremos incluidos

pytestmark = pytest.mark.skipif(not PARQUET.is_file(), reason="falta la verdad de terreno")


@pytest.fixture(autouse=True)
def real_data(monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(DATA))
    lectura.clear_cache()


def test_parquet_de_validacion_es_coherente():
    df = pl.read_parquet(PARQUET)
    assert df.columns == ["site_id", "utc", *frames.VALUE_COLUMNS]
    assert sorted(df["site_id"].unique().to_list()) == sorted(SITES)
    for sid in SITES:
        site = df.filter(pl.col("site_id") == sid)
        assert site.height == EXPECTED_ROWS
        assert site["utc"].is_sorted() and site["utc"].diff().drop_nulls().unique().to_list()[0].total_seconds() == 3600
        assert site[c.COL_SOL_AZ].is_between(0, 360, closed="left").all() and site[c.COL_TIERRA_EL].is_between(-90, 90).all()
        assert site.null_count().sum_horizontal().item() == 0


@pytest.mark.parametrize("site_id", SITES)
def test_la_verdad_empieza_donde_acaban_los_datos(site_id):
    """La primera fila de la validación es el último dato de entrenamiento: misma verdad de Horizons."""
    last = lectura.ephemeris(site_id)[-1]
    first = frames.from_parquet(PARQUET, site_id).collect().row(0, named=True)
    for column in frames.VALUE_COLUMNS:
        assert first[column] == pytest.approx(last[column], abs=1e-6)


@pytest.fixture(scope="module")
def evaluations():
    import os
    os.environ["DATA_DIR"] = str(DATA)
    lectura.clear_cache()
    return {sid: validation.evaluate_site(frames.from_rows(lectura.ephemeris(sid)), frames.from_parquet(PARQUET, sid))
            for sid in SITES}


@pytest.mark.parametrize("site_id", SITES)
def test_la_proyeccion_mantiene_un_error_razonable(evaluations, site_id):
    err = evaluations[site_id]["errores"]
    assert err[c.COL_TIERRA_EL]["rmse_deg"] < 1.0 and err[c.COL_TIERRA_AZ]["rmse_deg"] < 2.0
    assert err[c.COL_SOL_EL]["rmse_deg"] < 3.0
    assert err[c.COL_SOL_AZ]["rmse_deg"] < (12.0 if site_id == "apollo11" else 4.0)   # apollo11: Sol casi en el cénit


@pytest.mark.parametrize("site_id", SITES)
def test_la_libracion_proyectada_mejora_a_la_referencia_ingenua(evaluations, site_id):
    for column in (c.COL_TIERRA_AZ, c.COL_TIERRA_EL):
        e = evaluations[site_id]["errores"][column]
        assert e["rmse_deg"] < e["rmse_referencia_ingenua_deg"]


def test_el_semaforo_proyectado_acierta_la_mayoria_de_los_meses(evaluations):
    assert all(ev["semaforo"]["meses"] == 12 for ev in evaluations.values())
    assert sum(ev["semaforo"]["aciertos_global"] for ev in evaluations.values()) >= 105      # de 120
    assert evaluations["shackleton"]["semaforo"]["aciertos_global"] >= 11


def test_el_sol_polar_es_la_zona_dificil_para_las_puestas(evaluations):
    """Con el Sol rasante las puestas casi no se pueden emparejar; en latitudes medias sí."""
    assert evaluations["apollo15"]["puestas_sol"]["emparejados_pct"] == 100.0
    assert evaluations["apollo15"]["puestas_sol"]["mae_h"] < 4
    assert (evaluations["shackleton"]["puestas_sol"]["emparejados_pct"] or 0) < 50


def test_el_error_crece_con_el_horizonte_en_la_mayoria_de_variables(evaluations):
    grew = 0
    for ev in evaluations.values():
        by_month = ev["errores"][c.COL_SOL_EL]["rmse_por_mes_deg"]
        grew += by_month[-1] > by_month[0]
    assert grew >= 8


def test_la_tabla_de_errores_de_la_api_esta_al_dia(evaluations):
    """app/analysis/proyeccion_errores.json debe coincidir con una evaluación nueva (regenerar con analizar_proyeccion.py)."""
    table = json.loads(c.ERRORS_PATH.read_text(encoding="utf-8"))
    assert table[c.KEY_VERSION] == c.ERRORS_VERSION and sorted(table[c.KEY_SITES]) == sorted(SITES)
    for sid in SITES:
        for column in frames.VALUE_COLUMNS:
            stored, fresh = table["sitios"][sid][column], evaluations[sid]["errores"][column]
            assert stored["rmse_deg"] == pytest.approx(fresh["rmse_deg"], abs=1e-3)
            assert len(stored["rmse_por_mes_deg"]) == c.PROJECTION_MONTHS_AHEAD

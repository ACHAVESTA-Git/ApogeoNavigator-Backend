"""Pruebas de los archivos generados por scripts/datos/proyectar_2028.py (no se regenera nada salvo filas sueltas).

Filas esperadas por sitio: de 2027-06-01T01:00Z a 2028-07-31T23:00Z con paso de 1 h, ambos incluidos.
2027-06-01T00:00Z -> 2028-08-01T00:00Z son 427 días (366 hasta 2028-06-01, porque incluye el 29/02/2028, más 30 de
junio y 31 de julio) = 10 248 h. Se quita la hora 00:00Z del 2027-06-01 (es dato real) y la de 2028-08-01T00:00Z (no
se pide): 10 248 − 1 = 10 247 filas.
"""
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "proyecciones"
SITES = [line.split(",")[0] for line in (ROOT / "data" / "sites.csv").read_text(encoding="utf-8").splitlines()[1:]]
HEADER = ["utc", "sol_az_deg", "sol_el_deg", "tierra_az_deg", "tierra_el_deg"]
UTC_FORMAT = "%Y-%m-%dT%H:%MZ"
FIRST, LAST = "2027-06-01T01:00Z", "2028-07-31T23:00Z"
EXPECTED_ROWS = 10_247
DECIMALS = 4
AZIMUTHS = ("sol_az_deg", "tierra_az_deg")
EL_LIMIT, FULL_CIRCLE = 90.0, 360.0
SECONDS_PER_DAY = 86_400
REBUILD_TOL_DEG = 2e-4                 # redondeo a 4 decimales del CSV más margen numérico
REBUILD_ROWS = (0, EXPECTED_ROWS // 2, EXPECTED_ROWS - 1)
MODEL_KEYS = {"version", "metodo", "entrenado_desde", "entrenado_hasta", "proyectado_hasta", "error"}
ERROR_KEYS = {"rmse_deg", "por_horizonte", "control_horizons", "aciertos_semaforo_control"}
VARIABLES = HEADER[1:]
BODIES = {"sol": ("sol_az_deg", "sol_el_deg"), "tierra": ("tierra_az_deg", "tierra_el_deg")}

pytestmark = pytest.mark.skipif(not OUT.is_dir(), reason="sin proyecciones generadas (python scripts/datos/proyectar_2028.py)")


def parse(text: str) -> datetime:
    return datetime.strptime(text, UTC_FORMAT).replace(tzinfo=timezone.utc)


def read(site_id: str) -> tuple[str, list[list[str]]]:
    raw = (OUT / f"proyeccion_{site_id}.csv").read_text(encoding="utf-8")
    return raw, list(csv.reader(raw.splitlines()))


def load_json(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def test_hay_un_archivo_por_sitio():
    assert len(SITES) == 10
    assert sorted(p.name for p in OUT.glob("proyeccion_*.csv")) == sorted(f"proyeccion_{s}.csv" for s in SITES)


@pytest.mark.parametrize("site_id", SITES)
def test_formato_fechas_y_rangos(site_id):
    raw, rows = read(site_id)
    assert raw.endswith("\n") and not raw.endswith("\n\n") and "\r" not in raw
    assert rows[0] == HEADER
    data = rows[1:]
    assert len(data) == EXPECTED_ROWS
    times = [parse(r[0]) for r in data]
    assert data[0][0] == FIRST and data[-1][0] == LAST
    assert all(b - a == timedelta(hours=1) for a, b in zip(times, times[1:]))      # paso 1 h, sin huecos ni duplicados
    for row in data:
        assert len(row) == len(HEADER)
        for name, cell in zip(VARIABLES, row[1:]):
            assert len(cell.split(".")[1]) == DECIMALS
            value = float(cell)
            assert math.isfinite(value)
            assert (0 <= value < FULL_CIRCLE) if name in AZIMUTHS else (-EL_LIMIT <= value <= EL_LIMIT)


def test_modelo_json_claves_exactas():
    model = load_json("modelo.json")
    assert set(model) == MODEL_KEYS
    assert model["version"] == "armonico-v1" and model["proyectado_hasta"] == LAST
    assert model["entrenado_desde"] == "2026-12-01T00:00Z" and model["entrenado_hasta"] == "2027-06-01T00:00Z"
    assert set(model["error"]) == ERROR_KEYS
    assert model["error"]["rmse_deg"] == {v: None for v in ("sol_el_deg", "tierra_el_deg", "sol_az_deg", "tierra_az_deg")}
    assert all(model["error"][k] is None for k in ERROR_KEYS - {"rmse_deg"})


def _predict(model: dict, t: float, axes: list[str] | None) -> np.ndarray:
    """Evalúa un modelo de parametros_modelo.json en t (días desde t0) sin el código de app/."""
    pick = (lambda v: np.array([v[a] for a in axes])) if axes else (lambda v: np.array(v))
    out = pick(model["constante"]).astype(float)
    for term in model["terminos"]:
        phase = 2 * math.pi * term["frecuencia_ciclos_dia"] * t
        out = out + pick(term["cos"]) * math.cos(phase) + pick(term["sin"]) * math.sin(phase)
    return out


@pytest.mark.parametrize("site_id", SITES)
def test_parametros_reproducen_la_proyeccion(site_id):
    params = load_json("parametros_modelo.json")
    assert params["origen"] == "proyeccion" and params["version"] == "armonico-v1"
    site = params["sitios"][site_id]
    _, rows = read(site_id)
    t0, axes = parse(site["t0_utc"]), ["este", "norte", "arriba"]
    for index in REBUILD_ROWS:
        row = rows[1 + index]
        t = (parse(row[0]) - t0).total_seconds() / SECONDS_PER_DAY
        got = {}
        for body, (az, el) in BODIES.items():
            vector = _predict(site[body], t, axes)
            e, n, u = vector / np.linalg.norm(vector)
            got[az], got[el] = math.degrees(math.atan2(e, n)) % FULL_CIRCLE, math.degrees(math.asin(u))
        if site["sol_el_directo"] is not None:
            got["sol_el_deg"] = float(_predict(site["sol_el_directo"], t, None))
        for name, cell in zip(VARIABLES, row[1:]):
            diff = (got[name] - float(cell) + FULL_CIRCLE / 2) % FULL_CIRCLE - FULL_CIRCLE / 2
            assert abs(diff) <= REBUILD_TOL_DEG, (site_id, row[0], name, got[name], cell)


def test_holdout_rmse_finito():
    data = load_json("holdout_ultimo_mes.json")
    assert data["nota"] and data["entrenado_hasta_exclusivo"] == "2027-05-01T00:00Z"
    assert set(data["sitios"]) == set(SITES)
    for table in [*data["sitios"].values(), data["promedio_sitios"]]:
        for name in VARIABLES:
            assert math.isfinite(table[name]["rmse_deg"]) and math.isfinite(table[name]["mae_deg"])
            assert 0 <= table[name]["mae_deg"] <= table[name]["rmse_deg"]

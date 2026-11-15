#!/usr/bin/env python3
"""Revisión de datos reproducible de la verdad de terreno (Parquet) y de la tabla de errores de la API.

Uso (desde la raíz del backend):
    python scripts/datos/revisar_validacion.py              # comprobaciones locales
    python scripts/datos/revisar_validacion.py --horizons   # además, consultas de control propias a JPL Horizons

Imprime una línea por comprobación y termina con APROBADO (código 0) o CORREGIR (código 1). No modifica nada.
Las consultas a Horizons usan su propio código (no importan generar.py): observador coord@301, sin refracción.
"""
import csv
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / "scripts/datos/validacion/horizons_verdad.parquet"
RESULTS = ROOT / "scripts/datos/validacion/resultados.json"
ERRORS = ROOT / "app/analysis/proyeccion_errores.json"
DATA = ROOT / "data"
VARS = ["sol_az_deg", "sol_el_deg", "tierra_az_deg", "tierra_el_deg"]
COLUMNS = ["site_id", "utc", *VARS]
FIRST, LAST = datetime(2027, 6, 1, tzinfo=timezone.utc), datetime(2028, 6, 1, tzinfo=timezone.utc)
EXPECTED_ROWS = int((LAST - FIRST).total_seconds() / 3600) + 1
MAX_RATE_DEG_H = 1.0
MONTHS_AHEAD = 12
CONTROL = [("apollo11", 0.67416, 23.47314), ("shackleton", -89.66, 129.2), ("change6", -41.6385, -153.9852)]
CONTROL_DATES = ["2027-09-15 06:00", "2028-03-10 18:00"]
CONTROL_TOL_DEG = 1e-4
API = "https://ssd.jpl.nasa.gov/api/horizons.api"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'OK    ' if ok else 'FALLO '} {name}{(' · ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


def horizons(body: str, lat: float, lon: float, when: str) -> tuple[float, float]:
    start = datetime.strptime(when, "%Y-%m-%d %H:%M")
    q = {"format": "text", "COMMAND": f"'{body}'", "OBJ_DATA": "'NO'", "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "'OBSERVER'",
         "CENTER": "'coord@301'", "COORD_TYPE": "'GEODETIC'", "SITE_COORD": f"'{lon},{lat},0'",
         "START_TIME": f"'{when}'", "STOP_TIME": f"'{(start + timedelta(hours=1)):%Y-%m-%d %H:%M}'",
         "STEP_SIZE": "'1 h'", "QUANTITIES": "'4'", "CSV_FORMAT": "'YES'"}
    text = urllib.request.urlopen(API + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote), timeout=60).read().decode()
    row = text.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines()[0].split(",")
    return float(row[3]), float(row[4])


def review_parquet(df: pl.DataFrame) -> None:
    check("esquema del Parquet", df.columns == COLUMNS, str(df.columns))
    sites = sorted(df["site_id"].unique().to_list())
    check("10 sitios", len(sites) == 10, ", ".join(sites))
    check("sin nulos ni NaN", df.null_count().sum_horizontal().item() == 0
          and not any(df[v].is_nan().any() for v in VARS))
    for site in sites:
        s = df.filter(pl.col("site_id") == site).sort("utc")
        steps = s["utc"].diff().drop_nulls().unique().to_list()
        check(f"{site}: {EXPECTED_ROWS} filas de {FIRST:%Y-%m-%d} a {LAST:%Y-%m-%d} con paso de 1 h",
              s.height == EXPECTED_ROWS and steps == [timedelta(hours=1)] and s["utc"][0] == FIRST and s["utc"][-1] == LAST)
        check(f"{site}: azimut en [0, 360) y elevación en [-90, 90]",
              all(s[v].is_between(0, 360, closed="left").all() for v in ("sol_az_deg", "tierra_az_deg"))
              and all(s[v].is_between(-90, 90).all() for v in ("sol_el_deg", "tierra_el_deg")))
        jumps = {v: s[v].diff().abs().max() for v in ("sol_el_deg", "tierra_el_deg")}
        check(f"{site}: saltos de elevación ≤ {MAX_RATE_DEG_H}°/h", all(j <= MAX_RATE_DEG_H for j in jumps.values()),
              ", ".join(f"{k}={v:.3f}" for k, v in jumps.items()))
        with open(DATA / f"efemerides_{site}.csv", encoding="utf-8") as f:
            last = list(csv.DictReader(f))[-1]
        first = s.row(0, named=True)
        diff = max(abs(first[v] - float(last[v])) for v in VARS)
        check(f"{site}: la primera fila coincide con el último dato de entrenamiento", last["utc"] == "2027-06-01T00:00Z"
              and diff < 1e-6, f"diferencia máxima {diff:.1e}")


def review_errors_table() -> None:
    table = json.loads(ERRORS.read_text(encoding="utf-8"))
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    check("tabla de errores: misma lista de sitios que resultados.json", list(table["sitios"]) == list(results["sitios"]))
    bad = []
    for site, data in table["sitios"].items():
        for v in VARS:
            e, r = data[v], results["sitios"][site]["errores"][v]
            months = e["rmse_por_mes_deg"]
            if (len(months) != MONTHS_AHEAD or any(m is None or not math.isfinite(m) for m in months)
                    or e["rmse_deg"] != r["rmse_deg"] or e["mae_deg"] != r["mae_deg"] or months != r["rmse_por_mes_deg"]):
                bad.append(f"{site}/{v}")
    check(f"tabla de errores: 4 variables × {MONTHS_AHEAD} meses coherentes con resultados.json", not bad, ", ".join(bad[:3]))


def review_horizons(df: pl.DataFrame) -> None:
    for site, lat, lon in CONTROL:
        for when in CONTROL_DATES:
            stamp = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            row = df.filter((pl.col("site_id") == site) & (pl.col("utc") == stamp)).row(0, named=True)
            sun, earth = horizons("10", lat, lon, when), horizons("399", lat, lon, when)
            diff = max(abs(row["sol_az_deg"] % 360 - sun[0] % 360), abs(row["sol_el_deg"] - sun[1]),
                       abs(row["tierra_az_deg"] % 360 - earth[0] % 360), abs(row["tierra_el_deg"] - earth[1]))
            check(f"Horizons {site} {when}: Parquet = consulta de control", diff <= CONTROL_TOL_DEG, f"diferencia {diff:.1e}°")


def main() -> int:
    df = pl.read_parquet(PARQUET)
    review_parquet(df)
    review_errors_table()
    if "--horizons" in sys.argv:
        review_horizons(df)
    print("\nAPROBADO" if not failures else f"\nCORREGIR ({len(failures)} fallos)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

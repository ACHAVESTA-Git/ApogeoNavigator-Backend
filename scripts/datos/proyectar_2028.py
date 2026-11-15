#!/usr/bin/env python3
"""Proyección armónica del cielo lunar (Sol y Tierra) hora a hora hasta 2028 para los sitios del catálogo.

Usa el MISMO modelo que el análisis del backend (`app.analysis.projection.fit_projection`): ajuste armónico del vector
de dirección con los periodos sinódico (afinado con la FFT), anomalístico y dracónico, libración y bandas laterales.
Se entrena SOLO con nuestras efemérides (`data/efemerides_<site_id>.csv`); no usa la verdad de Horizons.

Escribe (en data/proyecciones/):
  * proyeccion_<site_id>.csv   utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg (paso 1 h, desde la hora
                               siguiente al último dato real hasta --hasta, ambos incluidos)
  * modelo.json                ficha del modelo con los errores en null (los rellena la validación)
  * parametros_modelo.json     frecuencias y coeficientes por sitio para reproducir la proyección sin reentrenar
  * holdout_ultimo_mes.json    solo con --holdout: entrena hasta 2027-05-01T00:00Z (exclusivo) y mide el último mes

Uso (desde la raíz del backend):
  python scripts/datos/proyectar_2028.py [--sitios apollo11,shackleton] [--hasta 2028-07-31T23:00Z] [--holdout]
Es una proyección estadística, no una efeméride: el error real a largo plazo lo mide la validación contra Horizons.
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))

import numpy as np                                            # noqa: E402

from app.analysis import frames, harmonic, projection         # noqa: E402
from app.core import config as c                              # noqa: E402
from app.services import lectura                              # noqa: E402

# ------------------------------------------------------------------ constantes del script
OUT_DIR = ROOT / "data" / "proyecciones"
CSV_NAME = "proyeccion_{site_id}.csv"
MODEL_JSON = "modelo.json"
PARAMS_JSON = "parametros_modelo.json"
HOLDOUT_JSON = "holdout_ultimo_mes.json"
MODEL_VERSION = "armonico-v1"
ORIGEN = "proyeccion"
DEFAULT_HASTA = "2028-07-31T23:00Z"
HOLDOUT_CUT = "2027-05-01T00:00Z"           # el entrenamiento del holdout termina justo antes (exclusivo)
STEP = timedelta(hours=1)
DECIMALS = 4                                # grados con 4 decimales en los CSV
FULL_CIRCLE = 360.0
EL_LIMIT = 90.0
METRIC_DECIMALS = 4                         # RMSE y MAE en los JSON
JSON_INDENT = 2
CSV_SEPARATOR = ","
NEWLINE = "\n"
ENCODING = "utf-8"
COLUMNS = (c.COL_UTC, *frames.VALUE_COLUMNS)
AZIMUTHS = (c.COL_SOL_AZ, c.COL_TIERRA_AZ)
VECTOR_AXES = ("este", "norte", "arriba")
PHYSICAL_JUMP_DEG_PER_H = 1.0               # límite físico del estándar CSV para |Δ elevación| por hora
METODO = ("Ajuste armónico por mínimos cuadrados del vector de dirección del Sol y de la Tierra con los periodos "
          "sinódico (afinado con la FFT), anomalístico y dracónico, libración y bandas laterales; entrenado solo "
          "con nuestras efemérides.")
ADVERTENCIA = "Proyección estadística con error medido; no es una efeméride."
EXTENSION = ("parametros_modelo.json: columnas origen y modelo_version del contrato inicial pasan aquí como "
             "metadatos; los CSV siguen el estándar de efemérides (5 columnas).")
COLUMN_ORDER_NOTE = ("prediccion(t) = constante + Σ cos·cos(2π·f·t) + sin·sin(2π·f·t), con t = días desde t0_utc. "
                     "Sol y Tierra: vector (este, norte, arriba) que se normaliza y se pasa a azimut = atan2(este, "
                     "norte) mod 360 y elevación = asin(arriba). sol_el_directo, si existe, sustituye a la elevación "
                     "del Sol del vector.")
HOLDOUT_NOTE = ("Comprobación interna: se entrena con nuestras efemérides hasta 2027-05-01T00:00Z (exclusivo) y se "
                "compara con nuestras propias efemérides del último mes. NO es el error real a largo plazo; ese lo "
                "mide la validación contra Horizons. Azimut con diferencia circular (±180°); elevación en grados.")
DIRECT_KEY = "sol_el_directo"


# ------------------------------------------------------------------ utilidades
def parse_utc(text: str) -> datetime:
    return datetime.strptime(text, c.UTC_FORMAT).replace(tzinfo=timezone.utc)


def hourly(start: datetime, end: datetime) -> list[datetime]:
    """Instantes de hora en hora de `start` a `end`, ambos incluidos."""
    count = int((end - start) / STEP) + 1
    return [start + STEP * i for i in range(count)]


def site_ids() -> list[str]:
    return [row[c.COL_SITE_ID] for row in lectura.sites_list()]


def load(site_id: str, before: datetime | None = None) -> list[lectura.Row]:
    """Filas validadas del sitio (mismo lector que la API); `before` recorta de forma exclusiva."""
    rows = lectura.ephemeris(site_id)
    return [r for r in rows if before is None or r[c.COL_UTC] < before]


def error(predicted: np.ndarray, actual: np.ndarray, column: str) -> np.ndarray:
    return harmonic.circular_error(predicted, actual) if column in AZIMUTHS else predicted - actual


def normalize(values: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Redondeo del CSV: azimut en [0, 360) (360,0000 pasa a 0), elevación en [-90, 90], sin −0."""
    out = {}
    for column, series in values.items():
        rounded = np.round(series, DECIMALS)
        if column in AZIMUTHS:
            rounded = np.round(np.mod(rounded, FULL_CIRCLE), DECIMALS)
            rounded[rounded >= FULL_CIRCLE] = 0.0
        else:
            rounded = np.clip(rounded, -EL_LIMIT, EL_LIMIT)
        out[column] = rounded + 0.0                # suma 0.0 para convertir −0.0 en 0.0
    return out


def write_csv(path: Path, times: list[datetime], values: dict[str, np.ndarray]) -> None:
    lines = [CSV_SEPARATOR.join(COLUMNS)]
    fmt = f"{{:.{DECIMALS}f}}"
    for i, moment in enumerate(times):
        cells = [lectura.fmt(moment)] + [fmt.format(values[col][i]) for col in frames.VALUE_COLUMNS]
        lines.append(CSV_SEPARATOR.join(cells))
    path.write_text(NEWLINE.join(lines) + NEWLINE, encoding=ENCODING)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=JSON_INDENT) + NEWLINE, encoding=ENCODING)


# ------------------------------------------------------------------ parámetros del modelo
def _names(terms: list[tuple[float, str]]) -> dict[float, str]:
    """Frecuencia plegada y redondeada como en `harmonic.fit` -> papel físico (une los que coinciden)."""
    out: dict[float, str] = {}
    for freq, role in terms:
        key = round(abs(freq), 9)
        out[key] = f"{out[key]} / {role}" if key in out else role
    return out


def _nearest_name(freq: float, names: dict[float, str]) -> str:
    return names[min(names, key=lambda f: abs(f - freq))] if names else c.ROLE_FALLBACK


def model_params(model: harmonic.HarmonicModel, names: dict[float, str], axes: tuple[str, ...] | None) -> dict:
    """Coeficientes en forma legible: constante y (cos, sin) por término; por eje si el modelo es vectorial."""
    coef = np.atleast_2d(model.coefficients.T).T          # (1 + 2·frecuencias) × columnas
    pick = (lambda row: dict(zip(axes, row.tolist()))) if axes else (lambda row: float(row[0]))
    terms = [{"nombre": names.get(freq) or _nearest_name(freq, names), "frecuencia_ciclos_dia": freq,
              "cos": pick(coef[1 + 2 * k]), "sin": pick(coef[2 + 2 * k])}
             for k, freq in enumerate(model.frequencies)]
    return {"constante": pick(coef[0]), "terminos": terms}


def site_params(fit: projection.SiteProjection, n_rows: int) -> dict:
    sun_names = _names(projection.sun_terms(1.0 / fit.synodic_days))
    params = {"t0_utc": lectura.fmt(fit.t0), "n_filas_entrenamiento": n_rows,
              "periodo_sinodico_dias": fit.synodic_days,
              "picos_fft_dias": [p.period_days for p in fit.peaks],
              projection.SUN: model_params(fit.models[projection.SUN], sun_names, VECTOR_AXES),
              projection.EARTH: model_params(fit.models[projection.EARTH], _names(projection.earth_terms()),
                                             VECTOR_AXES),
              DIRECT_KEY: None}
    if fit.sun_elevation_direct is not None:
        params[DIRECT_KEY] = model_params(fit.sun_elevation_direct, sun_names, None)
    return params


# ------------------------------------------------------------------ modos
def project(sites: list[str], hasta: datetime) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    params, summary, train_range = {}, [], None
    for sid in sites:
        rows = load(sid)
        fit = projection.fit_projection(frames.from_rows(rows))
        last = rows[-1]
        times = hourly(last[c.COL_UTC] + STEP, hasta)
        values = normalize(fit.predict_days(fit.days_of(times)))
        path = OUT_DIR / CSV_NAME.format(site_id=sid)
        write_csv(path, times, values)
        params[sid] = site_params(fit, len(rows))
        train_range = (lectura.fmt(rows[0][c.COL_UTC]), lectura.fmt(last[c.COL_UTC]))
        summary.append(controls(sid, fit, last, times, values, path))
    write_json(OUT_DIR / MODEL_JSON, {
        "version": MODEL_VERSION, "metodo": METODO,
        "entrenado_desde": train_range[0], "entrenado_hasta": train_range[1],
        "proyectado_hasta": lectura.fmt(hasta),
        "error": {"rmse_deg": {c.COL_SOL_EL: None, c.COL_TIERRA_EL: None, c.COL_SOL_AZ: None, c.COL_TIERRA_AZ: None},
                  "por_horizonte": None, "control_horizons": None, "aciertos_semaforo_control": None}})
    write_json(OUT_DIR / PARAMS_JSON, {
        "version": MODEL_VERSION, "origen": ORIGEN, "advertencia": ADVERTENCIA, "extension": EXTENSION,
        "generado_utc": lectura.fmt(datetime.now(timezone.utc).replace(second=0, microsecond=0)),
        "formula": COLUMN_ORDER_NOTE, "sitios": params})
    print_table(summary)


def controls(sid: str, fit: projection.SiteProjection, last: lectura.Row, times: list[datetime],
             values: dict[str, np.ndarray], path: Path) -> dict:
    """Continuidad en la unión (último dato real -> primera hora proyectada, y modelo en el último dato real),
    salto máximo de elevación por hora y tamaño del archivo."""
    at_last = fit.predict_days(fit.days_of([last[c.COL_UTC]]))
    out = {"sitio": sid, "filas": len(times), "bytes": path.stat().st_size,
           "periodo_sinodico": fit.synodic_days, "el_directa": fit.sun_elevation_direct is not None}
    for col in frames.VALUE_COLUMNS:
        real = np.array([last[col]])
        out[f"union_{col}"] = float(error(values[col][:1], real, col)[0])
        out[f"modelo_t_{col}"] = float(error(at_last[col], real, col)[0])
    for col in (c.COL_SOL_EL, c.COL_TIERRA_EL):
        out[f"salto_max_{col}"] = float(np.max(np.abs(np.diff(values[col]))))
    return out


def print_table(summary: list[dict]) -> None:
    print("sitio            filas   bytes  P_sin(d) el_dir | unión real->1.ª hora: sol_az sol_el tie_az tie_el"
          " | modelo-real en el último dato: sol_az sol_el tie_az tie_el | salto máx/h sol_el tie_el")
    for s in summary:
        union = " ".join(f"{s[f'union_{col}']:+7.3f}" for col in frames.VALUE_COLUMNS)
        model = " ".join(f"{s[f'modelo_t_{col}']:+7.3f}" for col in frames.VALUE_COLUMNS)
        jumps = " ".join(f"{s[f'salto_max_{col}']:.3f}" for col in (c.COL_SOL_EL, c.COL_TIERRA_EL))
        flag = "" if max(s[f"salto_max_{col}"] for col in (c.COL_SOL_EL, c.COL_TIERRA_EL)) <= PHYSICAL_JUMP_DEG_PER_H \
            else "  SUPERA 1°/h"
        print(f"{s['sitio']:<16}{s['filas']:>6}{s['bytes']:>8} {s['periodo_sinodico']:8.4f} {str(s['el_directa']):<6}"
              f"| {union} | {model} | {jumps}{flag}")


def metrics(errors: np.ndarray) -> dict[str, float]:
    return {"rmse_deg": round(float(np.sqrt(np.mean(errors ** 2))), METRIC_DECIMALS),
            "mae_deg": round(float(np.mean(np.abs(errors))), METRIC_DECIMALS)}


def holdout(sites: list[str]) -> dict:
    """Entrena hasta HOLDOUT_CUT (exclusivo) y mide RMSE y MAE en el último mes de nuestras efemérides."""
    cut = parse_utc(HOLDOUT_CUT)
    per_site: dict[str, dict] = {}
    for sid in sites:
        rows = lectura.ephemeris(sid)
        train = [r for r in rows if r[c.COL_UTC] < cut]
        test = [r for r in rows if r[c.COL_UTC] >= cut]
        fit = projection.fit_projection(frames.from_rows(train))
        predicted = fit.predict_days(fit.days_of([r[c.COL_UTC] for r in test]))
        per_site[sid] = {col: metrics(error(predicted[col], np.array([r[col] for r in test]), col))
                         for col in frames.VALUE_COLUMNS}
        per_site[sid]["n_filas_entrenamiento"], per_site[sid]["n_filas_prueba"] = len(train), len(test)
    mean = {col: {key: round(float(np.mean([per_site[s][col][key] for s in sites])), METRIC_DECIMALS)
                  for key in ("rmse_deg", "mae_deg")} for col in frames.VALUE_COLUMNS}
    first_test = lectura.fmt(cut)
    last_test = lectura.fmt(max(r[c.COL_UTC] for r in lectura.ephemeris(sites[0])))
    data = {"version": MODEL_VERSION, "nota": HOLDOUT_NOTE, "entrenado_hasta_exclusivo": HOLDOUT_CUT,
            "prueba_desde": first_test, "prueba_hasta": last_test, "sitios": per_site, "promedio_sitios": mean}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUT_DIR / HOLDOUT_JSON, data)
    print(f"Holdout {first_test} -> {last_test} (RMSE / MAE en grados)")
    print(f"{'sitio':<16}" + "".join(f"{col:>22}" for col in frames.VALUE_COLUMNS))
    for sid, row in [*per_site.items(), ("PROMEDIO", mean)]:
        print(f"{sid:<16}" + "".join(f"{row[col]['rmse_deg']:>12.4f} /{row[col]['mae_deg']:>8.4f}"
                                      for col in frames.VALUE_COLUMNS))
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split(NEWLINE)[0])
    parser.add_argument("--sitios", help="site_id separados por comas (por defecto, todos los de sites.csv)")
    parser.add_argument("--hasta", default=DEFAULT_HASTA, help="último instante proyectado, UTC AAAA-MM-DDTHH:MMZ")
    parser.add_argument("--holdout", action="store_true", help="solo la comprobación interna del último mes")
    args = parser.parse_args()
    sites = args.sitios.split(CSV_SEPARATOR) if args.sitios else site_ids()
    start = time.perf_counter()
    if args.holdout:
        holdout(sites)
    else:
        project(sites, parse_utc(args.hasta))
    print(f"Tiempo total: {time.perf_counter() - start:.1f} s")


if __name__ == "__main__":
    main()

"""Reglas de coherencia entre filas y archivos, una función por tipo.

Todas reciben los registros ya tipados (solo se ejecutan si no hay errores de celda).
"""
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from app.core import config as c
from app.validation.collector import Collector


@dataclass
class Record:
    row: int
    values: dict[str, Any]


@dataclass
class Context:
    known_site_ids: set[str] | None = None   # None = no existe sites.csv
    file_site_id: str | None = None          # site_id del nombre efemerides_<id>.csv
    extra: dict = field(default_factory=dict)


def _check_site_ref(site_id: str, row: int, column: str, ctx: Context, out: Collector) -> None:
    if ctx.known_site_ids is None:
        out.error(row, column, c.E_REFERENCIA, "No existe sites.csv: cárgalo primero", site_id)
    elif site_id not in ctx.known_site_ids:
        out.error(row, column, c.E_REFERENCIA, "El site_id no está en sites.csv", site_id)


def check_sites(records: list[Record], ctx: Context, out: Collector) -> None:
    tol = c.COORD_DUPLICATE_TOL_DEG
    for rec in records:
        v = rec.values
        if v["categoria"] == c.CATEGORIA_ARTEMIS and v["lat_deg"] > c.ARTEMIS_MAX_LAT:
            out.warning(rec.row, "lat_deg", c.W_ARTEMIS_LATITUD,
                        f"Una región Artemis debería estar al sur de {c.ARTEMIS_MAX_LAT:g}°", v["lat_deg"])
    ordered = sorted(records, key=lambda r: r.values["lat_deg"])
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if round(b.values["lat_deg"] - a.values["lat_deg"], 9) >= tol:
                break
            if round(abs(b.values["lon_este_deg"] - a.values["lon_este_deg"]), 9) < tol:
                later = max(a, b, key=lambda r: r.row)
                out.warning(later.row, "lat_deg", c.W_COORD_DUPLICADA,
                            "Coordenadas casi idénticas a otro sitio", later.values["lat_deg"])


def check_efemerides(records: list[Record], ctx: Context, out: Collector) -> None:
    first_row = records[0].row
    _check_site_ref(ctx.file_site_id or "", first_row - 1, "archivo", ctx, out)
    if len(records) < c.EPHEM_MIN_ROWS:
        out.error(first_row, "utc", c.E_COHERENCIA, f"Se necesitan al menos {c.EPHEM_MIN_ROWS} filas")
    seen: set = set()
    ordered = True
    for prev, cur in zip([None] + records[:-1], records):
        t = cur.values["utc"]
        if t in seen:
            out.error(cur.row, "utc", c.E_DUPLICADO, "utc repetido", cur.values["utc"].strftime(c.UTC_FORMAT))
            ordered = False
        elif prev is not None and t < prev.values["utc"]:
            out.error(cur.row, "utc", c.E_ORDEN, "utc debe ser estrictamente creciente",
                      t.strftime(c.UTC_FORMAT))
            ordered = False
        seen.add(t)
    if not ordered or len(records) < 2:
        return
    errors_before = out.total_errors
    step = records[1].values["utc"] - records[0].values["utc"]
    step_ok = timedelta(minutes=c.EPHEM_MIN_STEP_MINUTES) <= step <= timedelta(minutes=c.EPHEM_MAX_STEP_MINUTES)
    if not step_ok:
        out.error(records[1].row, "utc", c.E_PASO,
                  f"El paso debe estar entre {c.EPHEM_MIN_STEP_MINUTES} min y 24 h")
    for prev, cur in zip(records, records[1:]):
        if cur.values["utc"] - prev.values["utc"] != step:
            out.error(cur.row, "utc", c.E_PASO, "El paso temporal no es constante")
    if out.total_errors > errors_before:
        return
    if records[-1].values["utc"] - records[0].values["utc"] > timedelta(days=c.EPHEM_MAX_RANGE_DAYS):
        out.error(records[-1].row, "utc", c.E_COHERENCIA, f"El rango máximo es {c.EPHEM_MAX_RANGE_DAYS} días")
    hours = step.total_seconds() / 3600
    for prev, cur in zip(records, records[1:]):
        for column in ("sol_el_deg", "tierra_el_deg"):
            delta = abs(cur.values[column] - prev.values[column])
            if delta / hours > c.EPHEM_MAX_ELEVATION_RATE_DEG_H + 1e-9:
                out.error(cur.row, column, c.E_SALTO_FISICO,
                          f"Variación de {delta / hours:.2f}°/h; el máximo es {c.EPHEM_MAX_ELEVATION_RATE_DEG_H:g}°/h",
                          cur.values[column])


def check_metricas(records: list[Record], ctx: Context, out: Collector) -> None:
    seen: set = set()
    for rec in records:
        v = rec.values
        _check_site_ref(v["site_id"], rec.row, "site_id", ctx, out)
        key = (v["site_id"], v["inicio_utc"], v["fin_utc"])
        if key in seen:
            out.error(rec.row, "site_id", c.E_DUPLICADO, "Fila repetida (site_id, inicio_utc, fin_utc)", v["site_id"])
        seen.add(key)
        span = v["fin_utc"] - v["inicio_utc"]
        if span <= timedelta(0):
            out.error(rec.row, "fin_utc", c.E_COHERENCIA, "fin_utc debe ser posterior a inicio_utc")
            continue
        if span > timedelta(days=c.METRICS_MAX_RANGE_DAYS):
            out.error(rec.row, "fin_utc", c.E_COHERENCIA, f"El rango máximo es {c.METRICS_MAX_RANGE_DAYS} días")
            continue
        range_hours = span.total_seconds() / 3600
        for hours_col, pct_col in (("horas_sol", "pct_sol"), ("horas_tierra_visible", "pct_tierra")):
            hours = v[hours_col]
            if hours > range_hours + c.HOURS_TOL:
                out.error(rec.row, hours_col, c.E_RANGO, f"Debe estar entre 0 y {range_hours:g} (horas del rango)", hours)
            elif abs(v[pct_col] - hours / range_hours * 100) > c.METRICS_PCT_TOL:
                out.error(rec.row, pct_col, c.E_COHERENCIA,
                          f"No coincide con {hours_col} / horas del rango × 100", v[pct_col])
        mean, peak = v["sol_el_media_deg"], v["sol_el_max_deg"]
        if mean is not None and mean > peak:
            out.error(rec.row, "sol_el_media_deg", c.E_COHERENCIA, "La media no puede superar al máximo", mean)
        if mean is not None and v["horas_sol"] == 0:
            out.error(rec.row, "sol_el_media_deg", c.E_COHERENCIA, "Debe ir vacía si horas_sol = 0", mean)


COHERENCE: dict[str, Callable[[list[Record], Context, Collector], None]] = {
    c.TIPO_SITES: check_sites,
    c.TIPO_EFEMERIDES: check_efemerides,
    c.TIPO_METRICAS: check_metricas,
}

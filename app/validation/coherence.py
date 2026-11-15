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
        out.error(row, column, c.E_REFERENCIA, c.MSG_NO_SITES_FILE, site_id)
    elif site_id not in ctx.known_site_ids:
        out.error(row, column, c.E_REFERENCIA, c.MSG_SITE_NOT_FOUND, site_id)


def check_sites(records: list[Record], ctx: Context, out: Collector) -> None:
    tol = c.COORD_DUPLICATE_TOL_DEG
    for rec in records:
        v = rec.values
        if v[c.COL_CATEGORIA] == c.CATEGORIA_ARTEMIS and v[c.COL_LAT] > c.ARTEMIS_MAX_LAT:
            out.warning(rec.row, c.COL_LAT, c.W_ARTEMIS_LATITUD,
                        c.MSG_ARTEMIS_LAT.format(lat=c.ARTEMIS_MAX_LAT), v[c.COL_LAT])
    ordered = sorted(records, key=lambda r: r.values[c.COL_LAT])
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if round(b.values[c.COL_LAT] - a.values[c.COL_LAT], 9) >= tol:
                break
            if round(abs(b.values[c.COL_LON] - a.values[c.COL_LON]), 9) < tol:
                later = max(a, b, key=lambda r: r.row)
                out.warning(later.row, c.COL_LAT, c.W_COORD_DUPLICADA,
                            c.MSG_COORD_DUPLICATE, later.values[c.COL_LAT])


def check_efemerides(records: list[Record], ctx: Context, out: Collector) -> None:
    first_row = records[0].row
    _check_site_ref(ctx.file_site_id or "", first_row - 1, c.COL_ARCHIVO, ctx, out)
    if len(records) < c.EPHEM_MIN_ROWS:
        out.error(first_row, c.COL_UTC, c.E_COHERENCIA, c.MSG_EPHEM_MIN_ROWS.format(min=c.EPHEM_MIN_ROWS))
    seen: set = set()
    ordered = True
    for prev, cur in zip([None] + records[:-1], records):
        t = cur.values[c.COL_UTC]
        if t in seen:
            out.error(cur.row, c.COL_UTC, c.E_DUPLICADO, c.MSG_UTC_DUPLICATE, cur.values[c.COL_UTC].strftime(c.UTC_FORMAT))
            ordered = False
        elif prev is not None and t < prev.values[c.COL_UTC]:
            out.error(cur.row, c.COL_UTC, c.E_ORDEN, c.MSG_UTC_ORDER,
                      t.strftime(c.UTC_FORMAT))
            ordered = False
        seen.add(t)
    if not ordered or len(records) < 2:
        return
    errors_before = out.total_errors
    step = records[1].values[c.COL_UTC] - records[0].values[c.COL_UTC]
    step_ok = timedelta(minutes=c.EPHEM_MIN_STEP_MINUTES) <= step <= timedelta(minutes=c.EPHEM_MAX_STEP_MINUTES)
    if not step_ok:
        out.error(records[1].row, c.COL_UTC, c.E_PASO,
                  c.MSG_STEP_RANGE.format(min=c.EPHEM_MIN_STEP_MINUTES))
    for prev, cur in zip(records, records[1:]):
        if cur.values[c.COL_UTC] - prev.values[c.COL_UTC] != step:
            out.error(cur.row, c.COL_UTC, c.E_PASO, c.MSG_STEP_CONSTANT)
    if out.total_errors > errors_before:
        return
    if records[-1].values[c.COL_UTC] - records[0].values[c.COL_UTC] > timedelta(days=c.EPHEM_MAX_RANGE_DAYS):
        out.error(records[-1].row, c.COL_UTC, c.E_COHERENCIA, c.MSG_RANGE_DAYS.format(max=c.EPHEM_MAX_RANGE_DAYS))
    reference_steps = step.total_seconds() / (c.EPHEM_REFERENCE_STEP_MINUTES * c.SECONDS_PER_MINUTE)
    for prev, cur in zip(records, records[1:]):
        for column in (c.COL_SOL_EL, c.COL_TIERRA_EL):
            delta = abs(cur.values[column] - prev.values[column])
            if delta / reference_steps > c.EPHEM_MAX_ELEVATION_RATE_DEG_H + c.ELEVATION_RATE_TOL:
                out.error(cur.row, column, c.E_SALTO_FISICO,
                          c.MSG_PHYSICAL_JUMP.format(rate=delta / reference_steps, max=c.EPHEM_MAX_ELEVATION_RATE_DEG_H),
                          cur.values[column])


def check_metricas(records: list[Record], ctx: Context, out: Collector) -> None:
    seen: set = set()
    for rec in records:
        v = rec.values
        _check_site_ref(v[c.COL_SITE_ID], rec.row, c.COL_SITE_ID, ctx, out)
        key = (v[c.COL_SITE_ID], v[c.COL_INICIO], v[c.COL_FIN])
        if key in seen:
            out.error(rec.row, c.COL_SITE_ID, c.E_DUPLICADO, c.MSG_METRICS_DUPLICATE, v[c.COL_SITE_ID])
        seen.add(key)
        span = v[c.COL_FIN] - v[c.COL_INICIO]
        if span <= timedelta(0):
            out.error(rec.row, c.COL_FIN, c.E_COHERENCIA, c.MSG_END_BEFORE_START)
            continue
        if span > timedelta(days=c.METRICS_MAX_RANGE_DAYS):
            out.error(rec.row, c.COL_FIN, c.E_COHERENCIA, c.MSG_RANGE_DAYS.format(max=c.METRICS_MAX_RANGE_DAYS))
            continue
        range_hours = span.total_seconds() / c.SECONDS_PER_HOUR
        for hours_col, pct_col in ((c.COL_HORAS_SOL, c.COL_PCT_SOL), (c.COL_HORAS_TIERRA, c.COL_PCT_TIERRA)):
            hours = v[hours_col]
            if hours > range_hours + c.HOURS_TOL:
                out.error(rec.row, hours_col, c.E_RANGO, c.MSG_RANGE_HOURS.format(hi=range_hours), hours)
            elif abs(v[pct_col] - hours / range_hours * c.PCT_FULL_SCALE) > c.METRICS_PCT_TOL:
                out.error(rec.row, pct_col, c.E_COHERENCIA,
                          c.MSG_PCT_MISMATCH.format(hours=hours_col), v[pct_col])
        mean, peak = v[c.COL_SOL_EL_MEDIA], v[c.COL_SOL_EL_MAX]
        if mean is not None and mean > peak:
            out.error(rec.row, c.COL_SOL_EL_MEDIA, c.E_COHERENCIA, c.MSG_MEAN_GT_MAX, mean)
        if mean is not None and v[c.COL_HORAS_SOL] == 0:
            out.error(rec.row, c.COL_SOL_EL_MEDIA, c.E_COHERENCIA, c.MSG_MEAN_NOT_EMPTY, mean)


COHERENCE: dict[str, Callable[[list[Record], Context, Collector], None]] = {
    c.TIPO_SITES: check_sites,
    c.TIPO_EFEMERIDES: check_efemerides,
    c.TIPO_METRICAS: check_metricas,
}

"""Serie de Sol y Tierra para animar el cielo: datos reales dentro del rango y proyección fuera de él."""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from app.analysis import frames, harmonic, projection
from app.core import config as c
from app.services import lectura
from app.services.lectura import LecturaError

SECONDS_PER_DAY = c.SECONDS_PER_HOUR * c.HOURS_PER_DAY
VARIABLES = (c.COL_SOL_AZ, c.COL_SOL_EL, c.COL_TIERRA_AZ, c.COL_TIERRA_EL)


def site_model(site_id: str, rows: list[lectura.Row]) -> projection.SiteProjection:
    path = c.get_data_dir() / c.EFEMERIDES_FILENAME.format(site_id=site_id)

    def loader(_: Path) -> projection.SiteProjection:
        try:
            return projection.fit_projection(frames.from_rows(rows))
        except ValueError as exc:
            raise LecturaError(c.HTTP_UNPROCESSABLE, str(exc)) from None
    return lectura.cached_by_mtime(path, loader, c.CACHE_KIND_PROJECTION)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_errors_table(data) -> dict:
    """Valida la forma de la tabla de errores; cualquier otra cosa es un 503 con {"detail"}, nunca un KeyError."""
    sites = data.get(c.KEY_SITES) if isinstance(data, dict) else None
    if not isinstance(sites, dict) or data.get(c.KEY_VERSION) != c.ERRORS_VERSION:
        raise LecturaError(c.HTTP_UNAVAILABLE, c.MSG_SIM_ERRORS_INVALID)
    for table in sites.values():
        for column in VARIABLES:
            entry = table.get(column) if isinstance(table, dict) else None
            months = entry.get(c.KEY_RMSE_MONTHS) if isinstance(entry, dict) else None
            if (not isinstance(months, list) or len(months) != c.PROJECTION_MONTHS_AHEAD
                    or not all(m is None or _is_number(m) for m in months) or not _is_number(entry.get(c.KEY_RMSE_DEG))):
                raise LecturaError(c.HTTP_UNAVAILABLE, c.MSG_SIM_ERRORS_INVALID)
    return data


def _errors_data() -> dict:
    """Tabla de errores completa (sitios y metadatos), validada y en caché. Viaja con el código, no con `data/`:
    en producción solo se sincronizan los CSV y el manifiesto."""
    def loader(path: Path) -> dict:
        try:
            return _check_errors_table(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):                   # ilegible o JSON mal formado
            raise LecturaError(c.HTTP_UNAVAILABLE, c.MSG_SIM_ERRORS_INVALID) from None
    data = lectura.cached_by_mtime(c.ERRORS_PATH, loader, c.CACHE_KIND_ERRORS)
    if data is None:
        raise LecturaError(c.HTTP_UNAVAILABLE, c.MSG_SIM_ERRORS_MISSING)
    return data


def error_table(site_id: str) -> dict | None:
    """Errores de validación del sitio. None si el sitio no se validó."""
    return _errors_data()[c.KEY_SITES].get(site_id)


def error_meta() -> dict:
    """Metadatos de la validación (periodo de entrenamiento y de validación, fecha de generación)."""
    return {k: v for k, v in _errors_data().items() if k != c.KEY_SITES}


def _uncertainty(table: dict | None, months_ahead: np.ndarray) -> list[dict | None]:
    """Incertidumbre (RMSE de validación, grados) por punto según los meses de horizonte."""
    if not table:
        return [None] * len(months_ahead)
    out = []
    for month in months_ahead:
        entry = {}
        for column in VARIABLES:
            by_month = table[column][c.KEY_RMSE_MONTHS]
            entry[column] = by_month[month] if by_month[month] is not None else table[column][c.KEY_RMSE_DEG]
        out.append(entry)
    return out


def real_series(rows: list[lectura.Row], t_days: np.ndarray, t0: datetime) -> dict[str, np.ndarray]:
    """Datos reales a los instantes pedidos: interpola el vector de dirección (no los ángulos) y lo normaliza."""
    grid = np.array([(r[c.COL_UTC] - t0).total_seconds() / SECONDS_PER_DAY for r in rows])
    out: dict[str, np.ndarray] = {}
    for az_col, el_col in projection.COLUMNS.values():
        vectors = harmonic.direction_vector(np.array([r[az_col] for r in rows]), np.array([r[el_col] for r in rows]))
        interpolated = np.column_stack([np.interp(t_days, grid, vectors[:, k]) for k in range(3)])
        out[az_col], out[el_col] = harmonic.angles_of(interpolated)
    return out


def _resolve(rows: list[lectura.Row], desde: str | None, hasta: str | None, step: float) -> tuple[datetime, datetime]:
    first, last = rows[0][c.COL_UTC], rows[-1][c.COL_UTC]
    start = lectura.parse_instant(c.PARAM_DESDE, desde) or first
    end = lectura.parse_instant(c.PARAM_HASTA, hasta) or min(start + timedelta(days=c.SIM_DEFAULT_DAYS), last)
    if not c.SIM_MIN_STEP_HOURS <= step <= c.SIM_MAX_STEP_HOURS:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SIM_STEP.format(min=c.SIM_MIN_STEP_HOURS, max=c.SIM_MAX_STEP_HOURS))
    if start < first:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SIM_BEFORE_DATA)
    if end <= start:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_RANGE_ORDER)
    if end > last + timedelta(days=c.PROJECTION_MAX_DAYS):
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SIM_BEYOND_PROJECTION.format(days=c.PROJECTION_MAX_DAYS))
    points = int((end - start).total_seconds() / (step * c.SECONDS_PER_HOUR)) + 1
    if points > c.SIM_MAX_POINTS:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SIM_TOO_MANY_POINTS.format(points=points, max=c.SIM_MAX_POINTS))
    return start, end


def simulate(site_id: str, desde: str | None, hasta: str | None, paso_horas: float) -> dict:
    rows = lectura.ephemeris(site_id)            # 404 si el sitio o el archivo no existen; 503 si no validan
    start, end = _resolve(rows, desde, hasta, paso_horas)
    model = site_model(site_id, rows)
    count = int((end - start).total_seconds() / (paso_horas * c.SECONDS_PER_HOUR)) + 1
    times = [start + timedelta(hours=paso_horas * i) for i in range(count)]
    t_days = model.days_of(times)
    in_data = t_days <= model.last_t_days
    series = real_series(rows, t_days, model.t0)
    if not in_data.all():
        projected = model.predict_days(t_days)
        for column in VARIABLES:
            series[column] = np.where(in_data, series[column], projected[column])
    months = np.clip(np.floor((t_days - model.last_t_days) / c.MONTH_DAYS), 0,
                     c.PROJECTION_MONTHS_AHEAD - 1).astype(int)
    table = error_table(site_id) if not in_data.all() else None
    uncertainty = _uncertainty(table, months)
    points = [{c.COL_UTC: lectura.fmt(t), c.KEY_ORIGEN: c.ORIGEN_DATOS if inside else c.ORIGEN_PROYECCION,
               **{col: round(float(series[col][i]), c.ROUND_DEG) for col in VARIABLES},
               c.KEY_INCERTIDUMBRE: None if inside else uncertainty[i]}
              for i, (t, inside) in enumerate(zip(times, in_data))]
    warnings = []
    if not in_data.all():
        warnings.append({c.KEY_CODIGO: c.AVISO_PROYECCION, c.KEY_MENSAJE: c.MSG_SIM_WARNING})
        if table is None:
            warnings.append({c.KEY_CODIGO: c.AVISO_SIN_INCERTIDUMBRE, c.KEY_MENSAJE: c.MSG_SIM_NO_UNCERTAINTY})
        if max(r[c.COL_SOL_EL] for r in rows) > c.SIM_AZIMUTH_MAX_SUN_EL_DEG:
            warnings.append({c.KEY_CODIGO: c.AVISO_AZIMUT_CENIT, c.KEY_MENSAJE: c.MSG_SIM_AZIMUTH_WARNING})
    return {c.COL_SITE_ID: site_id, c.KEY_DESDE_UTC: lectura.fmt(start), c.KEY_HASTA_UTC: lectura.fmt(end),
            c.KEY_PASO_HORAS: paso_horas, c.KEY_FIN_DATOS: lectura.fmt(rows[-1][c.COL_UTC]),
            c.KEY_AVISOS: warnings, c.KEY_PUNTOS: points}

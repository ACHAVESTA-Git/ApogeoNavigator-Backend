"""Serie de Sol y Tierra para animar el cielo: datos reales dentro del rango y proyección fuera de él."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from app.analysis import frames, harmonic, projection
from app.core import config as c
from app.services import lectura
from app.services.lectura import LecturaError

SECONDS_PER_DAY = c.SECONDS_PER_HOUR * c.HOURS_PER_DAY
VARIABLES = (c.COL_SOL_AZ, c.COL_SOL_EL, c.COL_TIERRA_AZ, c.COL_TIERRA_EL)
MODEL_KIND, ERRORS_KIND = "proyeccion", "errores"


def _model(site_id: str, rows: list[lectura.Row]) -> projection.SiteProjection:
    path = c.get_data_dir() / c.EFEMERIDES_FILENAME.format(site_id=site_id)

    def loader(_: Path) -> projection.SiteProjection:
        try:
            return projection.fit_projection(frames.from_rows(rows))
        except ValueError as exc:
            raise LecturaError(c.HTTP_UNPROCESSABLE, str(exc)) from None
    return lectura.cached_by_mtime(path, loader, MODEL_KIND)


def _error_table(site_id: str) -> dict | None:
    """Errores de validación del sitio (`data/proyeccion_errores.json`), o None si no se han generado."""
    def loader(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("sitios", {})
        except (OSError, ValueError):
            return {}
    return (lectura.cached_by_mtime(c.get_data_dir() / c.ERRORS_FILENAME, loader, ERRORS_KIND) or {}).get(site_id)


def _uncertainty(table: dict | None, months_ahead: np.ndarray) -> list[dict | None]:
    """Incertidumbre (RMSE de la validación, grados) por punto según los meses de horizonte."""
    if not table:
        return [None] * len(months_ahead)
    out = []
    for month in months_ahead:
        entry = {}
        for column in VARIABLES:
            by_month = table[column]["rmse_por_mes_deg"]
            entry[column] = by_month[month] if by_month[month] is not None else table[column]["rmse_deg"]
        out.append(entry)
    return out


def _real_series(rows: list[lectura.Row], t_days: np.ndarray, t0: datetime) -> dict[str, np.ndarray]:
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
    model = _model(site_id, rows)
    count = int((end - start).total_seconds() / (paso_horas * c.SECONDS_PER_HOUR)) + 1
    times = [start + timedelta(hours=paso_horas * i) for i in range(count)]
    t_days = model.days_of(times)
    in_data = t_days <= model.last_t_days
    series = _real_series(rows, t_days, model.t0)
    if not in_data.all():
        projected = model.predict_days(t_days)
        for column in VARIABLES:
            series[column] = np.where(in_data, series[column], projected[column])
    months = np.clip(np.floor((t_days - model.last_t_days) / c.MONTH_DAYS), 0,
                     c.PROJECTION_MONTHS_AHEAD - 1).astype(int)
    uncertainty = _uncertainty(_error_table(site_id), months)
    points = [{c.COL_UTC: lectura.fmt(t), c.KEY_ORIGEN: c.ORIGEN_DATOS if inside else c.ORIGEN_PROYECCION,
               **{col: round(float(series[col][i]), c.ROUND_DEG) for col in VARIABLES},
               c.KEY_INCERTIDUMBRE: None if inside else uncertainty[i]}
              for i, (t, inside) in enumerate(zip(times, in_data))]
    warnings = []
    if not in_data.all():
        warnings.append({c.KEY_CODIGO: c.AVISO_PROYECCION, c.KEY_MENSAJE: c.MSG_SIM_WARNING})
        if max(r[c.COL_SOL_EL] for r in rows) > c.SIM_AZIMUTH_MAX_SUN_EL_DEG:
            warnings.append({c.KEY_CODIGO: c.AVISO_AZIMUT_CENIT, c.KEY_MENSAJE: c.MSG_SIM_AZIMUTH_WARNING})
    return {"site_id": site_id, c.KEY_DESDE_UTC: lectura.fmt(start), "hasta_utc": lectura.fmt(end),
            c.KEY_PASO_HORAS: paso_horas, c.KEY_FIN_DATOS: lectura.fmt(rows[-1][c.COL_UTC]),
            c.KEY_AVISOS: warnings, c.KEY_PUNTOS: points}

"""Lectura de efemérides, métricas recalculadas y puestas, con caché en memoria por archivo.

La caché se invalida sola cuando cambian la fecha de modificación o el tamaño del CSV
(por ejemplo, tras una carga en /api/datos). Los datos pasan por el mismo validador que
la API de carga, así que aquí solo llegan filas ya tipadas.
"""
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.core import config as c
from app.services import storage
from app.utils import dates
from app.utils.errors import CellError
from app.validation.coherence import Context
from app.validation.engine import validate_csv

Row = dict[str, Any]


class LecturaError(Exception):
    """Error de lectura con su código HTTP; el router lo convierte en {"detail": ...}."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------- caché por mtime
_cache: dict[Path, tuple[tuple[int, int], Any]] = {}
_lock = threading.Lock()


def _cached(path: Path, loader: Callable[[Path], Any]) -> Any | None:
    """Resultado de `loader(path)`; se recalcula si el archivo cambió. None si no existe."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        with _lock:
            _cache.pop(path, None)
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
    value = loader(path)
    with _lock:
        _cache[path] = (key, value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _records(tipo: str, ctx_factory: Callable[[], Context], bad_msg: str) -> Callable[[Path], list[Row]]:
    def loader(path: Path) -> list[Row]:
        try:
            raw = path.read_bytes()
        except OSError:                       # directorio, permisos, etc.
            raise LecturaError(c.HTTP_UNAVAILABLE, bad_msg) from None
        result = validate_csv(tipo, path.name, raw, ctx_factory())
        if not result.report.valido:
            raise LecturaError(c.HTTP_UNAVAILABLE, bad_msg)
        return [r.values for r in result.records]
    return loader


# ---------------------------------------------------------------- acceso a datos
def _sites() -> dict[str, Row]:
    path = c.get_data_dir() / c.SITES_FILENAME
    rows = _cached(path, _records(c.TIPO_SITES, Context, c.MSG_SITES_INVALID))
    if rows is None:
        raise LecturaError(c.HTTP_UNAVAILABLE, c.MSG_NO_SITES)
    return {r[c.COL_SITE_ID]: r for r in rows}


def require_site(site_id: str) -> None:
    if site_id not in _sites():
        raise LecturaError(c.HTTP_NOT_FOUND, c.MSG_SITE_NOT_IN_CATALOG)


def ephemeris(site_id: str) -> list[Row]:
    """Filas de efemérides del sitio (ordenadas por utc). 404 si el sitio o el archivo no existen."""
    require_site(site_id)
    path = c.get_data_dir() / c.EFEMERIDES_FILENAME.format(site_id=site_id)
    loader = _records(c.TIPO_EFEMERIDES, lambda: Context(known_site_ids={site_id}), c.MSG_EPHEM_INVALID)
    rows = _cached(path, loader)
    if rows is None:
        raise LecturaError(c.HTTP_NOT_FOUND, c.MSG_NO_EPHEMERIS)
    return rows


def _reference_metrics() -> list[Row]:
    """Filas de metricas.csv. Es solo una referencia opcional: si falta o no es válido, no bloquea."""
    path = c.get_data_dir() / c.METRICS_FILENAME
    ctx = lambda: Context(known_site_ids=set(_sites()))     # noqa: E731
    try:
        return _cached(path, _records(c.TIPO_METRICAS, ctx, c.MSG_METRICS_INVALID)) or []
    except LecturaError:
        return []


# ---------------------------------------------------------------- fechas y rangos
def fmt(moment: datetime) -> str:
    return moment.strftime(c.UTC_FORMAT)


def _instant(param: str, value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return dates.parse_utc(value)
    except CellError as exc:
        raise LecturaError(c.HTTP_UNPROCESSABLE, f"'{param}': {exc.message}") from None


def step_hours(rows: list[Row]) -> float:
    """Paso entre filas en horas (el validador garantiza que es constante)."""
    return (rows[1][c.COL_UTC] - rows[0][c.COL_UTC]).total_seconds() / c.SECONDS_PER_HOUR


def resolve_range(rows: list[Row], desde: str | None, hasta: str | None) -> tuple[datetime, datetime]:
    """Rango [desde, hasta] dentro de los datos y de como máximo QUERY_MAX_RANGE_DAYS días."""
    first, last = rows[0][c.COL_UTC], rows[-1][c.COL_UTC]
    start = _instant("desde", desde) or first
    end = _instant("hasta", hasta) or min(start + timedelta(days=c.QUERY_MAX_RANGE_DAYS), last)
    if not first <= start <= last:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_OUT_OF_DATA.format(param="desde"))
    if end > last:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_OUT_OF_DATA.format(param="hasta"))
    if end <= start:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_RANGE_ORDER)
    days = (end - start).total_seconds() / (c.SECONDS_PER_HOUR * c.HOURS_PER_DAY)
    if days > c.QUERY_MAX_RANGE_DAYS:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_QUERY_RANGE.format(max=c.QUERY_MAX_RANGE_DAYS, days=days))
    return start, end


# ---------------------------------------------------------------- efemérides
def ephemeris_range(site_id: str, desde: str | None, hasta: str | None) -> dict:
    rows = ephemeris(site_id)
    start, end = resolve_range(rows, desde, hasta)
    chosen = [r for r in rows if start <= r[c.COL_UTC] <= end]
    return {
        "site_id": site_id, "inicio_utc": fmt(start), "fin_utc": fmt(end), "paso_horas": step_hours(rows),
        "filas": [{**r, c.COL_UTC: fmt(r[c.COL_UTC])} for r in chosen],
    }


# ---------------------------------------------------------------- métricas
def _level(pct: float) -> str:
    if pct >= c.SEMAFORO_VERDE_MIN:
        return c.SEMAFORO_VERDE
    return c.SEMAFORO_AMARILLO if pct >= c.SEMAFORO_AMARILLO_MIN else c.SEMAFORO_ROJO


def traffic_light(pct_sol: float, pct_tierra: float) -> dict[str, str]:
    """Semáforo provisional: energía por % de Sol, comunicación por % de Tierra, global = el peor."""
    energy, comms = _level(pct_sol), _level(pct_tierra)
    order = (c.SEMAFORO_ROJO, c.SEMAFORO_AMARILLO, c.SEMAFORO_VERDE)
    return {"energia": energy, "comunicacion": comms, "global": min(energy, comms, key=order.index)}


def compute_metrics(site_id: str, rows: list[Row], step_h: float, start: datetime, end: datetime) -> dict:
    """Métricas de las muestras en [start, end). Una muestra cuenta `step_h` horas."""
    sample = [r for r in rows if start <= r[c.COL_UTC] < end]
    if not sample:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_NO_ROWS_IN_RANGE)
    sun = [r[c.COL_SOL_EL] for r in sample]
    earth = [r[c.COL_TIERRA_EL] for r in sample]
    sun_up = [e for e in sun if e > 0]
    total = len(sample) * step_h
    hours_sun, hours_earth = len(sun_up) * step_h, sum(1 for e in earth if e > 0) * step_h
    pct_sun = round(hours_sun / total * c.PCT_FULL_SCALE, 2)
    pct_earth = round(hours_earth / total * c.PCT_FULL_SCALE, 2)
    return {
        "site_id": site_id, "inicio_utc": fmt(start), "fin_utc": fmt(end),
        "horas_sol": hours_sun, "pct_sol": pct_sun, "sol_el_max_deg": round(max(sun), 4),
        "sol_el_media_deg": round(sum(sun_up) / len(sun_up), 4) if sun_up else None,
        "horas_tierra_visible": hours_earth, "pct_tierra": pct_earth,
        "semaforo": traffic_light(pct_sun, pct_earth), "origen": c.ORIGEN_CALCULADO,
    }


def _same(computed: Any, reference: Any, tol: float) -> bool:
    if computed is None or reference is None:
        return computed is reference
    return abs(computed - reference) <= tol


def _reference_for(result: dict, start: datetime, end: datetime) -> dict | None:
    """Fila de metricas.csv con el mismo sitio y rango, más si coincide con lo recalculado."""
    for ref in _reference_metrics():
        if (ref[c.COL_SITE_ID], ref[c.COL_INICIO], ref[c.COL_FIN]) == (result["site_id"], start, end):
            checks = (
                _same(result["horas_sol"], ref[c.COL_HORAS_SOL], c.HOURS_TOL),
                _same(result["horas_tierra_visible"], ref[c.COL_HORAS_TIERRA], c.HOURS_TOL),
                _same(result["pct_sol"], ref[c.COL_PCT_SOL], c.METRICS_PCT_TOL),
                _same(result["pct_tierra"], ref[c.COL_PCT_TIERRA], c.METRICS_PCT_TOL),
                _same(result["sol_el_max_deg"], ref[c.COL_SOL_EL_MAX], c.METRICS_DEG_TOL),
                _same(result["sol_el_media_deg"], ref[c.COL_SOL_EL_MEDIA], c.METRICS_DEG_TOL),
            )
            values = {k: ref[k] for k in (c.COL_HORAS_SOL, c.COL_PCT_SOL, c.COL_SOL_EL_MAX, c.COL_SOL_EL_MEDIA,
                                          c.COL_HORAS_TIERRA, c.COL_PCT_TIERRA)}
            return {**values, "coincide": all(checks)}
    return None


def metrics(site_ids: list[str], desde: str | None, hasta: str | None) -> dict:
    unique = list(dict.fromkeys(site_ids))
    if not unique:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SITE_ID_REQUIRED)
    if len(unique) > c.MAX_COMPARE_SITES:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_COMPARE_COUNT.format(max=c.MAX_COMPARE_SITES))
    results = []
    for site_id in unique:
        rows = ephemeris(site_id)
        start, end = resolve_range(rows, desde, hasta)
        result = compute_metrics(site_id, rows, step_hours(rows), start, end)
        result["referencia"] = _reference_for(result, start, end)
        results.append(result)
    return {"resultados": results}


# ---------------------------------------------------------------- puestas
def _events(rows: list[Row], idx: int, key: str, moment: datetime) -> dict:
    """Próxima puesta (elevación >0 → ≤0) y salida (≤0 → >0) desde la fila `idx`."""
    visible = rows[idx][key] > 0
    state, sunset, sunrise = visible, None, None
    for row in rows[idx + 1:]:
        now = row[key] > 0
        if now != state:
            if state:
                sunset = sunset or row[c.COL_UTC]
            else:
                sunrise = sunrise or row[c.COL_UTC]
            state = now
            if sunset and sunrise:
                break
    nxt = sunset if visible else sunrise
    hours = round((nxt - moment).total_seconds() / c.SECONDS_PER_HOUR, 2) if nxt else None
    return {
        "visible": visible,
        "proxima_puesta_utc": fmt(sunset) if sunset else None,
        "proxima_salida_utc": fmt(sunrise) if sunrise else None,
        "horas_restantes": hours,
    }


def sunsets(site_id: str, desde: str | None) -> dict:
    rows = ephemeris(site_id)
    moment = _instant("desde", desde) or rows[0][c.COL_UTC]
    if not rows[0][c.COL_UTC] <= moment <= rows[-1][c.COL_UTC]:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_OUT_OF_DATA.format(param="desde"))
    idx = max(i for i, r in enumerate(rows) if r[c.COL_UTC] <= moment)
    return {
        "site_id": site_id, "desde_utc": fmt(moment),
        "sol": _events(rows, idx, c.COL_SOL_EL, moment),
        "tierra": _events(rows, idx, c.COL_TIERRA_EL, moment),
    }

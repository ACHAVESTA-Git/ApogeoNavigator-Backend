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
    start = _instant(c.PARAM_DESDE, desde) or first
    end = _instant(c.PARAM_HASTA, hasta) or min(start + timedelta(days=c.QUERY_MAX_RANGE_DAYS), last)
    if not first <= start <= last:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_OUT_OF_DATA.format(param=c.PARAM_DESDE))
    if end > last:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_OUT_OF_DATA.format(param=c.PARAM_HASTA))
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
        c.COL_SITE_ID: site_id, c.COL_INICIO: fmt(start), c.COL_FIN: fmt(end), c.KEY_PASO_HORAS: step_hours(rows),
        c.KEY_FILAS: [{**r, c.COL_UTC: fmt(r[c.COL_UTC])} for r in chosen],
    }


# ---------------------------------------------------------------- métricas
def _level(pct: float, green_min: float, yellow_min: float) -> str:
    if pct >= green_min:
        return c.SEMAFORO_VERDE
    return c.SEMAFORO_AMARILLO if pct >= yellow_min else c.SEMAFORO_ROJO


def traffic_light(pct_sol: float, pct_tierra: float) -> dict[str, str]:
    """Semáforo provisional: energía por % de Sol, comunicación por % de Tierra (umbrales distintos), global = el peor."""
    energy = _level(pct_sol, c.SEMAFORO_ENERGIA_VERDE_MIN, c.SEMAFORO_ENERGIA_AMARILLO_MIN)
    comms = _level(pct_tierra, c.SEMAFORO_COMUNICACION_VERDE_MIN, c.SEMAFORO_COMUNICACION_AMARILLO_MIN)
    order = (c.SEMAFORO_ROJO, c.SEMAFORO_AMARILLO, c.SEMAFORO_VERDE)
    return {c.KEY_ENERGIA: energy, c.KEY_COMUNICACION: comms, c.KEY_GLOBAL: min(energy, comms, key=order.index)}


def warnings_for(sun_el_max: float) -> list[dict[str, str]]:
    """Avisos que acompañan al semáforo. Hoy: «sensible al relieve» si el Sol nunca supera 3°."""
    if sun_el_max < c.RELIEF_WARNING_MAX_SUN_EL_DEG:
        return [{c.KEY_CODIGO: c.AVISO_RELIEVE, c.KEY_MENSAJE: c.MSG_AVISO_RELIEVE.format(deg=c.RELIEF_WARNING_MAX_SUN_EL_DEG)}]
    return []


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
    pct_sun = round(hours_sun / total * c.PCT_FULL_SCALE, c.ROUND_PCT)
    pct_earth = round(hours_earth / total * c.PCT_FULL_SCALE, c.ROUND_PCT)
    return {
        c.COL_SITE_ID: site_id, c.COL_INICIO: fmt(start), c.COL_FIN: fmt(end),
        c.COL_HORAS_SOL: hours_sun, c.COL_PCT_SOL: pct_sun, c.COL_SOL_EL_MAX: round(max(sun), c.ROUND_DEG),
        c.COL_SOL_EL_MEDIA: round(sum(sun_up) / len(sun_up), c.ROUND_DEG) if sun_up else None,
        c.COL_HORAS_TIERRA: hours_earth, c.COL_PCT_TIERRA: pct_earth,
        c.KEY_SEMAFORO: traffic_light(pct_sun, pct_earth), c.KEY_AVISOS: warnings_for(max(sun)),
        c.KEY_ORIGEN: c.ORIGEN_CALCULADO,
    }


def _same(computed: Any, reference: Any, tol: float) -> bool:
    if computed is None or reference is None:
        return computed is reference
    return abs(computed - reference) <= tol


def _reference_for(result: dict, start: datetime, end: datetime) -> dict | None:
    """Fila de metricas.csv con el mismo sitio y rango, más si coincide con lo recalculado."""
    for ref in _reference_metrics():
        if (ref[c.COL_SITE_ID], ref[c.COL_INICIO], ref[c.COL_FIN]) == (result[c.COL_SITE_ID], start, end):
            checks = (
                _same(result[c.COL_HORAS_SOL], ref[c.COL_HORAS_SOL], c.HOURS_TOL),
                _same(result[c.COL_HORAS_TIERRA], ref[c.COL_HORAS_TIERRA], c.HOURS_TOL),
                _same(result[c.COL_PCT_SOL], ref[c.COL_PCT_SOL], c.METRICS_PCT_TOL),
                _same(result[c.COL_PCT_TIERRA], ref[c.COL_PCT_TIERRA], c.METRICS_PCT_TOL),
                _same(result[c.COL_SOL_EL_MAX], ref[c.COL_SOL_EL_MAX], c.METRICS_DEG_TOL),
                _same(result[c.COL_SOL_EL_MEDIA], ref[c.COL_SOL_EL_MEDIA], c.METRICS_DEG_TOL),
            )
            values = {k: ref[k] for k in (c.COL_HORAS_SOL, c.COL_PCT_SOL, c.COL_SOL_EL_MAX, c.COL_SOL_EL_MEDIA,
                                          c.COL_HORAS_TIERRA, c.COL_PCT_TIERRA)}
            return {**values, c.KEY_COINCIDE: all(checks)}
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
        result[c.KEY_REFERENCIA] = _reference_for(result, start, end)
        results.append(result)
    return {c.KEY_RESULTADOS: results}


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
    hours = round((nxt - moment).total_seconds() / c.SECONDS_PER_HOUR, c.ROUND_HOURS) if nxt else None
    return {
        c.KEY_VISIBLE: visible,
        c.KEY_PUESTA: fmt(sunset) if sunset else None,
        c.KEY_SALIDA: fmt(sunrise) if sunrise else None,
        c.KEY_HORAS_RESTANTES: hours,
    }


def sunsets(site_id: str, desde: str | None) -> dict:
    rows = ephemeris(site_id)
    moment = _instant(c.PARAM_DESDE, desde) or rows[0][c.COL_UTC]
    if not rows[0][c.COL_UTC] <= moment <= rows[-1][c.COL_UTC]:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_OUT_OF_DATA.format(param=c.PARAM_DESDE))
    idx = max(i for i, r in enumerate(rows) if r[c.COL_UTC] <= moment)
    return {
        c.COL_SITE_ID: site_id, c.KEY_DESDE_UTC: fmt(moment),
        c.KEY_SOL: _events(rows, idx, c.COL_SOL_EL, moment),
        c.KEY_TIERRA: _events(rows, idx, c.COL_TIERRA_EL, moment),
    }

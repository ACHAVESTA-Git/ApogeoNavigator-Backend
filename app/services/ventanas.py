"""Ventanas de alunizaje: para cada hora de llegada posible, cuánto Sol y Tierra hay durante la estancia."""
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta

from app.core import config as c
from app.services import lectura
from app.services.lectura import LecturaError
from app.utils import ventanas as util


def parse_stay(value: str | None) -> int:
    """`estancia_h` como entero de ESTANCIAS_H; si no, 422 ESTANCIA_INVALIDA."""
    try:
        stay = int(value) if value is not None else None
    except ValueError:
        stay = None
    if stay not in c.ESTANCIAS_H:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_STAY_INVALID.format(opciones=", ".join(map(str, c.ESTANCIAS_H))))
    return stay


def _pct(hours: float, stay_h: int) -> float:
    return round(hours / stay_h * c.PCT_FULL_SCALE, c.ROUND_PCT)


def _window(row: dict, stay_h: int) -> dict:
    """Fila del cálculo como ventana del contrato (instantes en formato UTC, porcentajes sobre la estancia)."""
    arrival: datetime = row[c.COL_UTC]
    return {
        c.KEY_LLEGADA: lectura.fmt(arrival), c.KEY_SALIDA_VENTANA: lectura.fmt(arrival + timedelta(hours=stay_h)),
        c.COL_HORAS_SOL: row[c.COL_HORAS_SOL], c.KEY_HORAS_TIERRA: row[c.KEY_HORAS_TIERRA],
        c.KEY_HORAS_SOL_Y_TIERRA: row[c.KEY_HORAS_SOL_Y_TIERRA],
        c.COL_PCT_SOL: _pct(row[c.COL_HORAS_SOL], stay_h), c.COL_PCT_TIERRA: _pct(row[c.KEY_HORAS_TIERRA], stay_h),
        c.KEY_PCT_SOL_Y_TIERRA: _pct(row[c.KEY_HORAS_SOL_Y_TIERRA], stay_h),
        c.KEY_SOL_AL_LLEGAR: row[c.KEY_SOL_AL_LLEGAR], c.KEY_TIERRA_AL_LLEGAR: row[c.KEY_TIERRA_AL_LLEGAR],
        c.KEY_RACHA_SIN_TIERRA: row[c.KEY_RACHA_SIN_TIERRA],
    }


def _site(site_id: str, stay_h: int, desde: str | None, hasta: str | None) -> tuple[dict, list[dict]]:
    """Resultado del sitio y su ventana mejor (en formato interno, para elegir el mejor global)."""
    rows = lectura.ephemeris(site_id)
    start, end = lectura.resolve_range(rows, desde, hasta, max_days=None)
    step_h = lectura.step_hours(rows)
    if stay_h % step_h:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_STAY_NOT_ON_GRID.format(paso=step_h))
    n = round(stay_h / step_h)
    sample = [r for r in rows if start <= r[c.COL_UTC] <= end]
    if not sample:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_NO_ROWS_IN_RANGE)
    candidates = util.evaluar_ventanas([r[c.COL_UTC] for r in sample], [r[c.COL_SOL_EL] for r in sample],
                                       [r[c.COL_TIERRA_EL] for r in sample], n, step_h)
    discarded = len(sample) - candidates.height
    ordered = util.ordenar_mejores(candidates)
    best = util.mejores_no_solapadas(ordered, n)
    daily = [
        {c.KEY_DIA_UTC: r[c.KEY_DIA_UTC], c.KEY_LLEGADA: lectura.fmt(r[c.COL_UTC]),
         c.KEY_HORAS_SOL_Y_TIERRA: r[c.KEY_HORAS_SOL_Y_TIERRA], c.KEY_PCT_SOL_Y_TIERRA: _pct(r[c.KEY_HORAS_SOL_Y_TIERRA], stay_h)}
        for r in util.mejor_por_dia(ordered)
    ]
    result = {
        c.COL_SITE_ID: site_id, c.COL_INICIO: lectura.fmt(start), c.COL_FIN: lectura.fmt(end), c.KEY_PASO_HORAS: step_h,
        c.KEY_VENTANAS_EVAL: candidates.height, c.KEY_VENTANAS_DESCARTADAS: discarded, c.KEY_TRUNCA: discarded > 0,
        c.KEY_SERIE_DIARIA: daily, c.KEY_MEJORES: [_window(r, stay_h) for r in best],
    }
    return result, best[:1]


_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _data_stamp(site_ids: list[str]) -> tuple:
    """Huella barata de los datos de los que depende el resultado: tamaño y mtime de las efemérides y del catálogo.
    Si cambian (una carga nueva), la entrada de caché deja de valer."""
    stamp = []
    for name in (c.SITES_FILENAME, *(c.EFEMERIDES_FILENAME.format(site_id=s) for s in site_ids)):
        try:
            st = (c.get_data_dir() / name).stat()
            stamp.append((name, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            stamp.append((name, None, None))
    return tuple(stamp)


def landing_windows(site_ids: list[str], estancia_h: str | None, desde: str | None, hasta: str | None) -> dict:
    """Con caché en memoria por (sitios, estancia, rango, datos) y TTL: pedir lo mismo no recalcula. Solo se guardan
    respuestas correctas; un error se vuelve a calcular (y a lanzar) cada vez."""
    unique = tuple(dict.fromkeys(site_ids))
    if len(unique) > c.MAX_VENTANAS_SITIOS or not all(c.SITE_ID_RE.fullmatch(s) for s in unique):
        return _landing_windows(list(unique), estancia_h, desde, hasta)       # lo rechaza o falla antes de tocar el disco; no se cachea
    key = (unique, estancia_h, desde, hasta, _data_stamp(list(unique)))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < c.VENTANAS_CACHE_TTL_S:
            _cache.move_to_end(key)
            return hit[1]
    result = _landing_windows(list(unique), estancia_h, desde, hasta)
    with _cache_lock:
        _cache[key] = (now, result)
        _cache.move_to_end(key)
        while len(_cache) > c.VENTANAS_CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
    return result


def _landing_windows(site_ids: list[str], estancia_h: str | None, desde: str | None, hasta: str | None) -> dict:
    unique = list(dict.fromkeys(site_ids))
    if not unique:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SITE_ID_REQUIRED)
    if len(unique) > c.MAX_VENTANAS_SITIOS:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_COMPARE_COUNT.format(max=c.MAX_VENTANAS_SITIOS))
    stay_h = parse_stay(estancia_h)
    results, tops = [], []
    for site_id in unique:
        result, best = _site(site_id, stay_h, desde, hasta)
        results.append(result)
        tops.extend((site_id, row) for row in best)
    global_best = None
    if len(unique) > 1 and tops:
        # Mismo orden que dentro de un sitio; a igualdad total gana el sitio pedido antes.
        site_id, row = min(tops, key=lambda t: (-t[1][c.KEY_HORAS_SOL_Y_TIERRA], -t[1][c.COL_HORAS_SOL], t[1][c.COL_UTC]))
        global_best = {c.COL_SITE_ID: site_id, c.KEY_VENTANA: _window(row, stay_h)}
    return {c.KEY_ESTANCIA: stay_h, c.KEY_RESULTADOS: results, c.KEY_MEJOR_GLOBAL: global_best}

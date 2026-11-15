"""Consulta EN VIVO a JPL Horizons para coordenadas propias (cualquier punto de la Luna).

Pide a Horizons el azimut y la elevación aparentes del Sol ('10') y de la Tierra ('399') vistos por un observador en la
superficie de la Luna (`coord@301`, coordenadas geodésicas, sin refracción), y devuelve las filas con el mismo formato que
`/api/efemerides`, más las métricas (con el semáforo) y las puestas, reutilizando `app.services.lectura`.

Limitaciones conocidas (aceptadas para esta fase):
  - Sin «single-flight»: dos peticiones idénticas simultáneas que no estén en caché hacen dos consultas a JPL; el límite de
    consultas y el semáforo acotan el daño.
  - La caché y los límites son POR INSTANCIA de Cloud Run. Con --max-instances=1 son exactos; con N instancias, N veces más.
  - El límite por cliente usa la IP que añade el balanceador (entrada `TRUSTED_PROXY_HOPS` desde el final de
    X-Forwarded-For); el límite global es la defensa real de JPL.

Cortesía hacia JPL: caché por (lat, lon redondeados, rango, paso), como máximo `HORIZONS_MAX_CONCURRENT` consultas a la
vez y un límite de consultas en vivo por ventana, global y por cliente. Los errores nunca reflejan el texto de JPL.
"""
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException

from app.core import config as c
from app.services import lectura
from app.services.lectura import LecturaError, Row
from app.utils.ratelimit import SlidingWindowLimiter

log = logging.getLogger(c.HORIZONS_LOGGER)

_DATE_RE = re.compile(r"(\d{4})-([A-Za-z]{3})-(\d{2}) (\d{2}):(\d{2})")
_HOUR = timedelta(hours=1)

_transport: httpx.BaseTransport | None = None            # solo para las pruebas (Horizons simulado, sin red)
_cache: "OrderedDict[tuple, tuple[float, list[Row]]]" = OrderedDict()
_cache_lock = threading.Lock()
_gate = threading.BoundedSemaphore(c.HORIZONS_MAX_CONCURRENT)
_global_limiter = SlidingWindowLimiter(*c.HORIZONS_RATE_GLOBAL)
_client_limiter = SlidingWindowLimiter(*c.HORIZONS_RATE_CLIENT)


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Sustituye el transporte HTTP (pruebas con `httpx.MockTransport`). `None` vuelve al real."""
    global _transport
    _transport = transport


def reset_state() -> None:
    """Vacía la caché y los contadores de límite (pruebas)."""
    with _cache_lock:
        _cache.clear()
    _global_limiter.reset()
    _client_limiter.reset()


def _fail(status: int, detail: str) -> LecturaError:
    return LecturaError(status, detail)


# ---------------------------------------------------------------- validación
def parse_range(desde: str, hasta: str, step_h: int) -> tuple[datetime, datetime]:
    """Valida el rango: fechas UTC válidas, dentro de los años admitidos, hasta > desde y como máximo 35 días."""
    start = lectura.parse_instant(c.PARAM_DESDE, desde)
    end = lectura.parse_instant(c.PARAM_HASTA, hasta)
    assert start is not None and end is not None
    for moment in (start, end):
        if not c.HORIZONS_MIN_YEAR <= moment.year <= c.HORIZONS_MAX_YEAR:
            raise _fail(c.HTTP_UNPROCESSABLE, c.MSG_HORIZONS_YEARS.format(min=c.HORIZONS_MIN_YEAR, max=c.HORIZONS_MAX_YEAR))
    if end <= start:
        raise _fail(c.HTTP_UNPROCESSABLE, c.MSG_RANGE_ORDER)
    days = (end - start).total_seconds() / (c.SECONDS_PER_HOUR * c.HOURS_PER_DAY)
    if days > c.QUERY_MAX_RANGE_DAYS:
        raise _fail(c.HTTP_UNPROCESSABLE, c.MSG_QUERY_RANGE.format(max=c.QUERY_MAX_RANGE_DAYS, days=days))
    return start, end


# ---------------------------------------------------------------- cliente HTTP con reintentos
def _params(body: str, lat: float, lon: float, start: datetime, end: datetime, step_h: int) -> dict[str, str]:
    return {
        "format": "text", "COMMAND": f"'{body}'", "OBJ_DATA": "'NO'", "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "'OBSERVER'",
        "CENTER": f"'{c.HORIZONS_CENTER}'", "COORD_TYPE": f"'{c.HORIZONS_COORD_TYPE}'",
        "SITE_COORD": f"'{lon},{lat},0'",                                      # longitud primero (Este positivo)
        "START_TIME": f"'{start.strftime(c.HORIZONS_TIME_FORMAT)}'", "STOP_TIME": f"'{end.strftime(c.HORIZONS_TIME_FORMAT)}'",
        "STEP_SIZE": f"'{step_h} h'", "QUANTITIES": f"'{c.HORIZONS_QUANTITIES}'", "CSV_FORMAT": "'YES'",
    }


def _read_capped(response: httpx.Response, where: str) -> str:
    """Lee el cuerpo (ya descomprimido) hasta `HORIZONS_MAX_RESPONSE_BYTES`; si lo supera, 502 sin seguir leyendo."""
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > c.HORIZONS_MAX_RESPONSE_BYTES:
            log.warning(c.MSG_HORIZONS_LOG_REJECTED, where, "respuesta demasiado grande")
            raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_UNEXPECTED)
    return body.decode(response.encoding or "utf-8", errors="replace")


def _get_text(client: httpx.Client, params: dict[str, str], where: str) -> str:
    """GET con reintentos. Timeout -> 504; cualquier otro fallo del servicio externo -> 502."""
    last: Exception | None = None
    for attempt in range(c.HORIZONS_RETRIES + 1):
        if attempt:
            time.sleep(c.HORIZONS_RETRY_BACKOFF_S)
        try:
            with client.stream("GET", c.HORIZONS_URL, params=params) as response:
                if response.status_code in c.HORIZONS_RETRY_STATUS:
                    last = httpx.HTTPStatusError(str(response.status_code), request=response.request, response=response)
                    continue
                if response.status_code != c.HTTP_OK:
                    log.warning(c.MSG_HORIZONS_LOG_FAILED, f"HTTP {response.status_code}", where)
                    raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_FAILED)
                return _read_capped(response, where)
        except httpx.TimeoutException as exc:
            last = exc
        except httpx.RequestError as exc:                 # transporte, DecodingError, TooManyRedirects...
            last = exc
    log.warning(c.MSG_HORIZONS_LOG_FAILED, type(last).__name__, where)
    if isinstance(last, httpx.TimeoutException):
        raise _fail(c.HTTP_GATEWAY_TIMEOUT, c.MSG_HORIZONS_TIMEOUT)
    raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_FAILED)


# ---------------------------------------------------------------- interpretación de la respuesta
def _number(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(token)
    return value


def parse_ephemeris(text: str, where: str) -> list[tuple[datetime, float, float]]:
    """Filas (utc, azimut, elevación) entre $$SOE y $$EOE, tras comprobar Luna (301) y sin refracción."""
    if c.HORIZONS_SOE not in text or c.HORIZONS_EOE not in text:
        log.warning(c.MSG_HORIZONS_LOG_REJECTED, where, " ".join(text.split())[:c.HORIZONS_LOG_SNIPPET_CHARS])
        raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_UNEXPECTED)
    if c.HORIZONS_HEADER_MOON not in text or c.HORIZONS_HEADER_AIRLESS not in text:
        raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_NOT_MOON)
    head, block = text.split(c.HORIZONS_SOE, 1)
    if not all(column in head for column in c.HORIZONS_HEADER_COLUMNS):
        raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_UNEXPECTED)
    block = block.split(c.HORIZONS_EOE, 1)[0]
    rows: list[tuple[datetime, float, float]] = []
    try:
        for line in block.splitlines():
            if not line.strip():
                continue
            tokens = [t.strip() for t in line.split(",")]
            while tokens and not tokens[-1]:
                tokens.pop()
            match = _DATE_RE.fullmatch(tokens[0])
            if match is None or len(tokens) < 3 or match.group(2) not in c.HORIZONS_MONTHS:
                raise ValueError(line)
            year, month, day, hour, minute = (int(match.group(1)), c.HORIZONS_MONTHS[match.group(2)],
                                              int(match.group(3)), int(match.group(4)), int(match.group(5)))
            azimuth, elevation = _number(tokens[-2]), _number(tokens[-1])
            if not c.AZ_MIN <= azimuth <= c.AZ_MAX or not c.EL_MIN <= elevation <= c.EL_MAX:
                raise ValueError(line)
            rows.append((datetime(year, month, day, hour, minute, tzinfo=timezone.utc), azimuth, elevation))
    except (ValueError, IndexError):
        log.warning(c.MSG_HORIZONS_LOG_REJECTED, where, c.MSG_HORIZONS_BAD_ROW_LOG)
        raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_UNEXPECTED) from None
    if not rows:
        raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_UNEXPECTED)
    return rows


def _fetch(lat: float, lon: float, start: datetime, end: datetime, step_h: int) -> list[Row]:
    where = f"{lat},{lon}"
    timeout = httpx.Timeout(c.HORIZONS_TIMEOUT_S, connect=c.HORIZONS_CONNECT_TIMEOUT_S)
    headers = {"User-Agent": c.HORIZONS_USER_AGENT}
    if not _gate.acquire(timeout=c.HORIZONS_GATE_WAIT_S):        # no se bloquea un hilo de FastAPI indefinidamente
        raise HTTPException(status_code=c.HTTP_UNAVAILABLE, detail=c.MSG_HORIZONS_BUSY.format(seconds=c.HORIZONS_GATE_RETRY_AFTER_S),
                            headers={c.HORIZONS_RETRY_AFTER_HEADER: str(c.HORIZONS_GATE_RETRY_AFTER_S)})
    try:
        with httpx.Client(timeout=timeout, headers=headers, transport=_transport) as client:
            sun = parse_ephemeris(_get_text(client, _params(c.HORIZONS_BODY_SUN, lat, lon, start, end, step_h), where), where)
            earth = parse_ephemeris(_get_text(client, _params(c.HORIZONS_BODY_EARTH, lat, lon, start, end, step_h), where), where)
    finally:
        _gate.release()
    if [r[0] for r in sun] != [r[0] for r in earth]:
        raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_UNEXPECTED)
    expected_max = int((end - start) / (step_h * _HOUR)) + 2
    if len(sun) > expected_max:
        raise _fail(c.HTTP_BAD_GATEWAY, c.MSG_HORIZONS_UNEXPECTED)
    return [{c.COL_UTC: s[0], c.COL_SOL_AZ: s[1], c.COL_SOL_EL: s[2], c.COL_TIERRA_AZ: e[1], c.COL_TIERRA_EL: e[2]}
            for s, e in zip(sun, earth)]


# ---------------------------------------------------------------- caché y límites
def _cached(key: tuple) -> list[Row] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if entry[0] < time.monotonic():
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return entry[1]


def _store(key: tuple, rows: list[Row]) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + c.HORIZONS_CACHE_TTL_S, rows)
        _cache.move_to_end(key)
        while len(_cache) > c.HORIZONS_CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def _check_rate(client: str | None) -> None:
    wait = max(_global_limiter.peek(), _client_limiter.peek(client))
    if wait <= 0:
        _global_limiter.hit()
        _client_limiter.hit(client)
        return
    seconds = max(1, math.ceil(wait))
    raise HTTPException(status_code=c.HTTP_TOO_MANY_REQUESTS, detail=c.MSG_HORIZONS_TOO_MANY.format(seconds=seconds),
                        headers={c.HORIZONS_RETRY_AFTER_HEADER: str(seconds)})


# ---------------------------------------------------------------- consulta
def query(lat: float, lon: float, desde: str, hasta: str, step_h: int, client: str | None = None) -> dict[str, Any]:
    """Efemérides en vivo, métricas y puestas de un punto de la Luna. Las coordenadas se redondean a
    `HORIZONS_COORD_DECIMALS` decimales (lo que se consulta y se devuelve)."""
    start, end = parse_range(desde, hasta, step_h)
    lat, lon = round(lat, c.HORIZONS_COORD_DECIMALS), round(lon, c.HORIZONS_COORD_DECIMALS)
    key = (lat, lon, start, end, step_h)
    rows = _cached(key)
    from_cache = rows is not None
    if rows is None:
        _check_rate(client)
        rows = _fetch(lat, lon, start, end, step_h)
        _store(key, rows)
    metrics = lectura.compute_metrics(c.CUSTOM_SITE_ID, rows, float(step_h), start, end)
    metrics[c.KEY_REFERENCIA] = None
    metrics[c.KEY_ORIGEN] = c.ORIGEN_HORIZONS
    return {
        c.COL_SITE_ID: c.CUSTOM_SITE_ID, c.COL_INICIO: lectura.fmt(start), c.COL_FIN: lectura.fmt(end),
        c.KEY_PASO_HORAS: float(step_h), c.KEY_FILAS: [{**r, c.COL_UTC: lectura.fmt(r[c.COL_UTC])} for r in rows],
        c.KEY_FUENTE: c.HORIZONS_SOURCE, c.COL_LAT: lat, c.COL_LON: lon, c.KEY_EN_CACHE: from_cache,
        c.KEY_CONSULTA_UTC: lectura.fmt(datetime.now(timezone.utc)),
        c.KEY_METRICAS: metrics, c.KEY_PUESTAS: lectura.sunsets_from_rows(c.CUSTOM_SITE_ID, rows, start),
    }

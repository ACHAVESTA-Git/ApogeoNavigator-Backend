"""Logs legibles: una línea por petición y, en los 5xx, la traza enlazada por request_id. LOG_FORMAT=texto|json y LOG_LEVEL."""
import json
import logging
import os
import time
from datetime import datetime, timezone

from app.core import config as c
from app.utils.text import escapar_control

_MARCA = "_apogeo_handler"


class FormatoTexto(logging.Formatter):
    """`2026-11-15T04:12:33Z INFO  <mensaje>`; la traza (si hay) va debajo."""

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, timezone.utc).strftime(c.LOG_TS_FORMAT)

    def format(self, record):
        base = f"{self.formatTime(record)} {record.levelname:<5} {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


class FormatoJson(logging.Formatter):
    """Una línea JSON por registro (Cloud Logging la entiende): ts, nivel, logger, mensaje y los campos de la petición."""

    def format(self, record):
        salida = {"ts": datetime.fromtimestamp(record.created, timezone.utc).strftime(c.LOG_TS_FORMAT),
                  "nivel": record.levelname, "logger": record.name, "mensaje": record.getMessage()}
        salida.update(getattr(record, "campos", {}))
        if record.exc_info:
            salida["traza"] = self.formatException(record.exc_info)
        return json.dumps(salida, ensure_ascii=False)


def configurar_logging() -> None:
    """Instala (una sola vez, de forma idempotente) el manejador del logger `apogeo` según LOG_FORMAT y LOG_LEVEL."""
    formato = os.getenv(c.ENV_LOG_FORMAT, c.DEFAULT_LOG_FORMAT).strip().lower()
    if formato not in c.LOG_FORMATS:
        formato = c.DEFAULT_LOG_FORMAT
    nivel = logging.getLevelName(os.getenv(c.ENV_LOG_LEVEL, c.DEFAULT_LOG_LEVEL).strip().upper())
    logger = logging.getLogger(c.LOGGER_ROOT)
    for viejo in [h for h in logger.handlers if getattr(h, _MARCA, False)]:
        logger.removeHandler(viejo)
    handler = logging.StreamHandler()
    handler.setFormatter(FormatoJson() if formato == "json" else FormatoTexto())
    setattr(handler, _MARCA, True)
    logger.addHandler(handler)
    logger.setLevel(nivel if isinstance(nivel, int) else logging.INFO)


def nivel_para(estado: int) -> int:
    """2xx y 3xx en INFO; 401 y 404 en INFO (son normales); el resto de 4xx en WARNING; 5xx en ERROR."""
    if estado >= 500:
        return logging.ERROR
    if estado >= 400 and estado not in c.LOG_QUIET_STATUSES:
        return logging.WARNING
    return logging.INFO


def linea_acceso(request_id: str, metodo: str, ruta: str, estado: int, codigo: str | None, ms: float, accion: str | None) -> str:
    """`<request_id> GET  /api/x → 404 CODIGO (3 ms) | acción` (sin query string: nunca claves ni datos del cliente)."""
    metodo, ruta = escapar_control(metodo, 16), escapar_control(ruta, c.LOG_PATH_MAX)
    cuerpo = f"{request_id} {metodo:<4} {ruta} → {estado} {codigo or 'OK'} ({ms:.0f} ms)"
    return f"{cuerpo} | {accion}" if accion else cuerpo


def registrar_acceso(logger: logging.Logger, request_id, metodo, ruta, estado, codigo, ms, accion) -> None:
    metodo, ruta = escapar_control(metodo, 16), escapar_control(ruta, c.LOG_PATH_MAX)
    logger.log(nivel_para(estado), linea_acceso(request_id, metodo, ruta, estado, codigo, ms, accion),
               extra={"campos": {"request_id": request_id, "metodo": metodo, "ruta": ruta, "estado": estado,
                                 "codigo": codigo or "OK", "ms": round(ms), "accion": accion}})


def ahora_ms() -> float:
    return time.perf_counter() * 1000

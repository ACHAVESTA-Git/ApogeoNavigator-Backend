"""Manejadores de error centralizados: toda respuesta de error es JSON con `detail` (como siempre) y un objeto `error`
estandarizado (código, título, mensaje, acción, request_id) del catálogo `app/core/mensajes.py`.

`detail` no cambia: el frontend y el contrato congelado dependen de su forma (texto, lista de FastAPI o informe de CSV).
Nunca se exponen trazas, SQL ni rutas internas en la respuesta; eso va al log del servidor con el mismo request_id.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import config
from app.core.respuestas import request_id_de, respuesta_error
from app.services.blobstore import StoreError
from app.services.errores import ErrorServicio
from app.utils.text import escapar_control

log = logging.getLogger(config.LOGGER_ERRORS)

# Errores de CONEXIÓN con la base de datos: el servicio no está disponible (503). El resto de SQLAlchemyError son fallos
# de programación o de datos: 500 genérico, sin filtrar el SQL ni los parámetros al cliente.
DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, DisconnectionError, PoolTimeoutError)


async def http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTPException de los routers y 404/405 propios de FastAPI (rutas inexistentes o método no permitido)."""
    return respuesta_error(request, exc.status_code, exc.detail, headers=dict(exc.headers or {}))


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 de validación nativo: `detail` es la lista de siempre; `error.mensaje` resume los campos sin citar sus valores."""
    campos = list(dict.fromkeys(str(e["loc"][-1]) for e in exc.errors() if e["loc"] and str(e["loc"][-1]) not in config.VALIDATION_LOC_NOISE))
    resumen = (config.MSG_VALIDATION_FIELDS.format(n=len(campos), campos=", ".join(campos)) if campos else config.MSG_VALIDATION_GENERIC)
    return respuesta_error(request, config.HTTP_UNPROCESSABLE, jsonable_encoder(exc.errors()), mensaje=resumen)


async def servicio_error(request: Request, exc: ErrorServicio) -> JSONResponse:
    return respuesta_error(request, exc.status, exc.detail, headers=exc.headers)


async def store_error(request: Request, exc: StoreError) -> JSONResponse:
    log.error(config.MSG_STORE_ERROR_LOG, request_id_de(request), request.method, escapar_control(request.url.path, config.LOG_PATH_MAX), type(exc).__name__)
    return respuesta_error(request, config.HTTP_UNAVAILABLE, config.MSG_BUCKET_UNAVAILABLE)


async def db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    if isinstance(exc, DB_UNAVAILABLE_ERRORS):
        log.error(config.MSG_DB_ERROR_LOG, request_id_de(request), request.method, escapar_control(request.url.path, config.LOG_PATH_MAX), type(exc).__name__)
        return respuesta_error(request, config.HTTP_UNAVAILABLE, config.MSG_DB_UNAVAILABLE)
    return await unhandled(request, exc)


async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Traza completa al log (enlazada por request_id) y nunca al cliente.
    log.error(config.MSG_UNHANDLED_LOG, request_id_de(request), request.method, escapar_control(request.url.path, config.LOG_PATH_MAX), exc_info=exc)
    return respuesta_error(request, config.HTTP_INTERNAL_ERROR, config.MSG_INTERNAL_ERROR)


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(ErrorServicio, servicio_error)
    app.add_exception_handler(StoreError, store_error)
    app.add_exception_handler(SQLAlchemyError, db_error)
    app.add_exception_handler(Exception, unhandled)

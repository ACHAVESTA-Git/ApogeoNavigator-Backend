"""Manejadores de error centralizados: toda respuesta de error de la API es JSON `{"detail": ...}`.

No se tocan los 422 de validación nativos de FastAPI (`detail` en lista) ni el formato del 413 del guardia de subida: el
frontend y el contrato congelado dependen de ellos.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core import config
from app.services.blobstore import StoreError
from app.services.lectura import LecturaError

log = logging.getLogger(config.LOGGER_ERRORS)

# Errores de CONEXIÓN con la base de datos: el servicio no está disponible (503). El resto de SQLAlchemyError son fallos
# de programación o de datos: 500 genérico, sin filtrar el SQL ni los parámetros al cliente.
DB_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, DisconnectionError, PoolTimeoutError)


def _json(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


async def lectura_error(_: Request, exc: LecturaError) -> JSONResponse:
    return _json(exc.status, exc.detail)


async def store_error(_: Request, exc: StoreError) -> JSONResponse:
    return _json(config.HTTP_UNAVAILABLE, config.MSG_BUCKET_UNAVAILABLE)


async def db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    if isinstance(exc, DB_UNAVAILABLE_ERRORS):
        log.error(config.MSG_DB_ERROR_LOG, request.method, request.url.path, type(exc).__name__)
        return _json(config.HTTP_UNAVAILABLE, config.MSG_DB_UNAVAILABLE)
    return await unhandled(request, exc)


async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.error(config.MSG_UNHANDLED_LOG, request.method, request.url.path, exc_info=exc)   # traza al log, nunca al cliente
    return _json(config.HTTP_INTERNAL_ERROR, config.MSG_INTERNAL_ERROR)


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(LecturaError, lectura_error)
    app.add_exception_handler(StoreError, store_error)
    app.add_exception_handler(SQLAlchemyError, db_error)
    app.add_exception_handler(Exception, unhandled)

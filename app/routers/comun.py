"""Piezas comunes de los routers de consulta: descripciones de error y traducción de LecturaError a HTTP."""
from collections.abc import Callable

from fastapi import HTTPException

from app.core import config as c
from app.services.lectura import LecturaError

RESPONSES = {
    c.HTTP_NOT_FOUND: {"description": c.DESC_NOT_FOUND},
    c.HTTP_UNPROCESSABLE: {"description": c.DESC_UNPROCESSABLE},
    c.HTTP_UNAVAILABLE: {"description": c.DESC_UNAVAILABLE},
}


def run_service(fn: Callable, *args):
    """Ejecuta un servicio de lectura y convierte su LecturaError en un HTTPException con {"detail"}."""
    try:
        return fn(*args)
    except LecturaError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None

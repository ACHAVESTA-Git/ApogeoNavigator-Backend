"""DTO comunes: error, página genérica y fecha UTC."""
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, StringConstraints

from app.core import config

T = TypeVar("T")


class ErrorOut(BaseModel):
    """Cuerpo de un error. `detail` es un texto en los errores de la API y en el 401 del guardia, una lista en los 422
    de validación nativos de FastAPI y un informe de CSV (objeto) en los 422 de la carga de archivos.
    `codigo` solo lo lleva el 413 del guardia de subida (E_TAMANO)."""
    detail: str | list | dict
    codigo: str | None = None


class Page(BaseModel, Generic[T]):
    """Página de resultados: `{total, limite, offset, items}`."""
    total: int
    limite: int
    offset: int
    items: list[T]


# Fecha UTC del contrato de datos: YYYY-MM-DDTHH:MMZ (sin segundos). Es str a propósito: datetime la serializaría distinto.
UtcStr = Annotated[str, StringConstraints(pattern=config.UTC_FIELD_PATTERN)]

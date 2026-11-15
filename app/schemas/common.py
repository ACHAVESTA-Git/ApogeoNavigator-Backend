"""DTO comunes: error, página genérica y fecha UTC."""
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, StringConstraints

from app.core import config

T = TypeVar("T")


class ErrorOut(BaseModel):
    """Cuerpo de un error. `detail` es un texto en los errores de la API y en el 401 del guardia, una lista en los 422
    de validación nativos de FastAPI y un informe de CSV (objeto) en los 422 de la carga de archivos."""
    detail: str | list | dict


class Page(BaseModel, Generic[T]):
    """Página de resultados: `{total, limite, offset, items}`."""
    total: int
    limite: int
    offset: int
    items: list[T]


# Fecha UTC del contrato de datos: YYYY-MM-DDTHH:MMZ (sin segundos). Es str a propósito: datetime la serializaría distinto.
UtcStr = Annotated[str, StringConstraints(pattern=config.UTC_FIELD_PATTERN)]

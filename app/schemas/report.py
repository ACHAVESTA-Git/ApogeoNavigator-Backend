"""Modelos Pydantic del informe de validación (formato del §5 del estándar)."""
from typing import Literal

from pydantic import BaseModel, Field

from app.core import config

TipoArchivo = Literal[*config.TIPOS_ARCHIVO]


class Issue(BaseModel):
    fila: int          # los errores de archivo (sin fila propia) usan HEADER_ROW (1)
    columna: str | None = None
    codigo: str
    mensaje: str
    valor: str | None = None


class Report(BaseModel):
    archivo: str
    tipo: TipoArchivo
    valido: bool
    total_filas: int = 0
    total_errores: int = 0
    errores: list[Issue] = Field(default_factory=list)
    advertencias: list[Issue] = Field(default_factory=list)


class SaveResult(Report):
    guardado: bool = False
    copia_historial: str | None = None

"""Modelos Pydantic del informe de validación (formato del §5 del estándar)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import config

TipoArchivo = Literal[*config.TIPOS_ARCHIVO]


class Issue(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"fila": 2, "columna": "lat_deg", "codigo": "E_RANGO", "mensaje": "Debe estar entre -90 y 90", "valor": "91"}]})

    fila: int = Field(description="File row (1 = header, 2 = first data row). File-level errors use 1.")
    columna: str | None = Field(None, description="Affected column; null if the error belongs to the row or the file.")
    codigo: str = Field(description="Stable code: E_* blocks the upload, W_* is a warning.")
    mensaje: str = Field(description="Human-readable explanation of the problem.")
    valor: str | None = Field(None, description="Value received, truncated to 50 characters.")


class Report(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "archivo": "sites.csv", "tipo": "sites", "valido": False, "total_filas": 3, "total_errores": 1,
        "errores": [{"fila": 2, "columna": "lat_deg", "codigo": "E_RANGO", "mensaje": "Debe estar entre -90 y 90", "valor": "91"}],
        "advertencias": []}]})

    archivo: str = Field(description="Name of the uploaded file (sanitised).")
    tipo: TipoArchivo = Field(description="Data type: sites, efemerides or metricas.")
    valido: bool = Field(description="true if there are no errors (warnings do not block).")
    total_filas: int = Field(0, description="Data rows read (header excluded).")
    total_errores: int = Field(0, description="Errors found; `errores` lists at most the first 100.")
    errores: list[Issue] = Field(default_factory=list, description="Errors that prevent the file from being saved.")
    advertencias: list[Issue] = Field(default_factory=list, description="Warnings that do not block the upload.")


class ErrorValidacionOut(BaseModel):
    """Cuerpo de un 422 en la carga: el informe de un archivo rechazado, o la lista de errores nativa de FastAPI si la
    petición está mal formada (por ejemplo, sin el campo `file`)."""
    detail: Report | list


class SaveResult(Report):
    guardado: bool = False
    copia_historial: str | None = None

"""Modelos Pydantic del informe de validación (formato del §5 del estándar)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import config

TipoArchivo = Literal[*config.TIPOS_ARCHIVO]


class Issue(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"fila": 2, "columna": "lat_deg", "codigo": "E_RANGO", "mensaje": "Debe estar entre -90 y 90", "valor": "91"}]})

    fila: int = Field(description="Fila del archivo (1 = cabecera, 2 = primer dato). Los errores de archivo usan 1.")
    columna: str | None = Field(None, description="Columna afectada; null si el error es de la fila o del archivo.")
    codigo: str = Field(description="Código estable: E_* bloquea la carga, W_* es una advertencia.")
    mensaje: str = Field(description="Explicación legible del problema.")
    valor: str | None = Field(None, description="Valor recibido, recortado a 50 caracteres.")


class Report(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "archivo": "sites.csv", "tipo": "sites", "valido": False, "total_filas": 3, "total_errores": 1,
        "errores": [{"fila": 2, "columna": "lat_deg", "codigo": "E_RANGO", "mensaje": "Debe estar entre -90 y 90", "valor": "91"}],
        "advertencias": []}]})

    archivo: str = Field(description="Nombre del archivo subido (saneado).")
    tipo: TipoArchivo = Field(description="Tipo de datos: sites, efemerides o metricas.")
    valido: bool = Field(description="true si no hay ningún error (las advertencias no bloquean).")
    total_filas: int = Field(0, description="Filas de datos leídas (sin la cabecera).")
    total_errores: int = Field(0, description="Errores encontrados; `errores` lista como mucho los 100 primeros.")
    errores: list[Issue] = Field(default_factory=list, description="Errores que impiden guardar el archivo.")
    advertencias: list[Issue] = Field(default_factory=list, description="Avisos que no bloquean la carga.")


class ErrorValidacionOut(BaseModel):
    """Cuerpo de un archivo rechazado: el informe va dentro de `detail`."""
    detail: Report


class SaveResult(Report):
    guardado: bool = False
    copia_historial: str | None = None

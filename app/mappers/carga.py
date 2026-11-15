"""Conversión del resultado de una carga guardada a su DTO."""
from pathlib import Path

from app.schemas.report import Report, SaveResult


def guardado_out(informe: Report, copia_historial: Path | None) -> SaveResult:
    return SaveResult(**informe.model_dump(), guardado=True, copia_historial=copia_historial.name if copia_historial else None)

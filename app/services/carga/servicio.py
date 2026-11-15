"""Contrato del servicio de carga de CSV (lo que ven el router y las pruebas con colaboradores falsos)."""
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from app.db.enums import AccionAuditoria
from app.schemas.report import SaveResult
from app.validation.engine import ValidationResult


@dataclass
class Revision:
    """Resultado de validar un archivo más los bytes leídos (a lo sumo MAX_FILE_BYTES + 1), necesarios para auditar rechazos."""
    resultado: ValidationResult
    bruto: bytes


class CargaService(Protocol):
    def revisar(self, tipo: str, nombre: str | None, archivo: BinaryIO) -> Revision: ...
    def auditar(self, tipo: str, revision: Revision, accion: AccionAuditoria, origen: object) -> None: ...
    def guardar_carga(self, tipo: str, revision: Revision, origen: object) -> SaveResult: ...

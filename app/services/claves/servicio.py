"""Contrato del servicio de claves (lo que ven los routers y las pruebas con repositorios falsos)."""
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.models.claves import ClaveUsuario, EstadoClave, TipoClave
from app.schemas.claves import ClaveOut, Entrega, GenerarIn, HuellaOut, PaginaClaves, PublicaOut, RevocarIn, RotarIn


@dataclass
class EntregaPendiente:
    """Resultado de generar o rotar: el cuerpo a devolver y lo que hay que cerrar DESPUÉS de enviar la respuesta."""
    entrega: Entrega
    ids: list[uuid.UUID]
    buffers: list[bytearray] = field(repr=False)


class Auditor(Protocol):
    def registrar(self, operacion: str, clave: ClaveUsuario, motivo: str | None = None, extra: dict | None = None) -> None: ...


class ClavesService(Protocol):
    def listar(self, usuario_id: uuid.UUID | None, estado: EstadoClave | None, tipo: TipoClave | None, q: str | None,
               limite: int, offset: int) -> PaginaClaves: ...
    def generar(self, cuerpo: GenerarIn) -> EntregaPendiente: ...
    def publica(self, clave_id: uuid.UUID, formato: str) -> PublicaOut: ...
    def huella(self, clave_id: uuid.UUID) -> HuellaOut: ...
    def revocar(self, clave_id: uuid.UUID, cuerpo: RevocarIn) -> ClaveOut: ...
    def rotar(self, clave_id: uuid.UUID, cuerpo: RotarIn | None) -> EntregaPendiente: ...
    def cerrar_entrega(self, pendiente: EntregaPendiente) -> None: ...

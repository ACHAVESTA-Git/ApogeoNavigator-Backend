"""Modelos del gestor de claves PQC (módulo de admin). La clave privada solo aparece en `ClaveEntregada`, una vez."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core import config as c
from app.models.claves import EstadoClave, TipoClave
from app.schemas.common import Page

PUBLICA_DESCRIPTION = (
    "With `formato=json` (default) a `PublicaOut` JSON body. With `formato=pem` the public key as a downloadable PEM file "
    "(`Content-Disposition: attachment`).")
PUBLICA_PEM_CONTENT = {"schema": {"type": "string", "format": "binary"},
                       "example": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"}
FORMATO_DESCRIPTION = f"Response format: `{c.KEYS_FORMAT_JSON}` (default) or `{c.KEYS_FORMAT_PEM}` (PEM file download)."


class ClaveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    usuario_id: uuid.UUID
    usuario_email: str | None = None
    usuario_nombre: str | None = None
    tipo: TipoClave
    algoritmo: str
    estado: EstadoClave
    huella_sha3_256: str
    tamano_publica: int
    tamano_privada: int = Field(description="Bytes of the delivered private key (seed); it is not stored")
    tamano_privada_fips: int = Field(description="Size of the expanded private key according to the standard")
    creada_utc: datetime
    revocada_utc: datetime | None
    motivo_revocacion: str | None
    reemplazada_por: uuid.UUID | None
    backups: int = Field(description="Copies of the private key delivered: 0 (not delivered) or 1")


PaginaClaves = Page[ClaveOut]


class ClaveEntregada(ClaveOut):
    clave_publica_pem: str
    privada_pem: str = Field(description="Delivered ONCE; it cannot be recovered")
    nombre_archivo_privada: str


class Entrega(BaseModel):
    aviso: str
    claves: list[ClaveEntregada]


class GenerarIn(BaseModel):
    usuario_id: uuid.UUID
    tipos: list[TipoClave] = Field(default_factory=lambda: [TipoClave.KEM, TipoClave.FIRMA], min_length=1, max_length=2)


class RevocarIn(BaseModel):
    motivo: str = Field(min_length=c.KEYS_MOTIVE_MIN, max_length=c.KEYS_MOTIVE_MAX)


class RotarIn(BaseModel):
    motivo: str = Field(default=c.KEYS_MOTIVE_ROTATION, min_length=c.KEYS_MOTIVE_MIN, max_length=c.KEYS_MOTIVE_MAX)


class PublicaOut(BaseModel):
    id: uuid.UUID
    tipo: TipoClave
    algoritmo: str
    estado: EstadoClave
    huella_sha3_256: str
    clave_publica_pem: str
    clave_publica_b64: str


class HuellaOut(BaseModel):
    id: uuid.UUID
    algoritmo: str
    estado: EstadoClave
    huella_sha3_256: str

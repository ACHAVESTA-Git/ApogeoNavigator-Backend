"""Modelos del login local. Ninguno contiene material privado: solo huellas de claves públicas."""
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.core import config as c
from app.db.enums import RolUsuario
from app.models.claves import EstadoClave, TipoClave


class LoginIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"email": "analista@apogeo.test", "password": "••••••••"}]})
    email: str = Field(min_length=3, max_length=c.AUTH_EMAIL_MAX, description="E-mail of the account (case-insensitive).")
    password: str = Field(min_length=1, max_length=c.AUTH_PASSWORD_MAX, description="Account password. Never logged or stored.")


class ClaveResumenOut(BaseModel):
    id: uuid.UUID
    tipo: TipoClave
    algoritmo: str = Field(description="ML-KEM-1024 (encryption) or ML-DSA-65 (signature).")
    estado: EstadoClave
    huella_sha3_256: str = Field(description="SHA3-256 fingerprint of the PUBLIC key (hexadecimal). Private keys are never returned.")


class UsuarioActualOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "6f1c1c1e-6a52-4c53-a4f0-3b6c6c3d9a10", "nombre": "Analista de prueba", "rol": "analista",
        "claves": [{"id": "0d9f2c1a-4b7e-4f43-9d0e-6d7c1a2b3c4d", "tipo": "kem", "algoritmo": "ML-KEM-1024", "estado": "activa",
                    "huella_sha3_256": "9b2f0c4d7e1a3b5c6d8e9f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e"}]}]})
    id: uuid.UUID
    nombre: str
    rol: RolUsuario
    claves: list[ClaveResumenOut] = Field(description="Active public keys of the user (fingerprints only).")

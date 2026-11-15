"""Contrato del servicio de autenticación."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.db.models import Usuario
from app.schemas.auth import UsuarioActualOut


@dataclass
class SesionNueva:
    """El token en claro solo existe aquí: va a la cookie y no se guarda en ningún sitio."""
    token: str
    expira_utc: datetime
    usuario: UsuarioActualOut


class Auditor(Protocol):
    def login(self, usuario_id, origen: object) -> None: ...
    def login_fallido(self, motivo: str, origen: object) -> None: ...
    def logout(self, usuario_id, origen: object) -> None: ...


class AuthService(Protocol):
    def login(self, email: str, password: str, origen: object, token_previo: str | None) -> SesionNueva: ...
    def logout(self, token: str | None, origen: object) -> None: ...
    def autenticar(self, token: str | None) -> Usuario | None: ...
    def perfil(self, usuario: Usuario) -> UsuarioActualOut: ...

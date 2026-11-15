"""Acceso a datos del login: usuarios por email, sesiones por hash del token y claves públicas del usuario."""
import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Sesion, Usuario
from app.models.claves import ClaveUsuario, EstadoClave
from app.services import usuarios as servicio_usuarios


class AuthRepository(Protocol):
    def usuario_por_email(self, email: str) -> Usuario | None: ...
    def sesion_por_hash(self, token_hash: str) -> Sesion | None: ...
    def crear_sesion(self, usuario_id: uuid.UUID, token_hash: str, expira_utc: datetime, ip_hash: str | None,
                     user_agent: str | None) -> Sesion: ...
    def claves_activas(self, usuario_id: uuid.UUID) -> list[ClaveUsuario]: ...
    def commit(self) -> None: ...


class SqlAuthRepository:
    def __init__(self, db: Session):
        self.db = db

    def usuario_por_email(self, email: str) -> Usuario | None:
        return servicio_usuarios.buscar_por_email(self.db, email)

    def sesion_por_hash(self, token_hash: str) -> Sesion | None:
        return self.db.scalar(select(Sesion).where(Sesion.token_hash == token_hash))

    def crear_sesion(self, usuario_id, token_hash, expira_utc, ip_hash, user_agent) -> Sesion:
        sesion = Sesion(usuario_id=usuario_id, token_hash=token_hash, expira_utc=expira_utc, ip_hash=ip_hash, user_agent=user_agent)
        self.db.add(sesion)
        self.db.flush()
        return sesion

    def claves_activas(self, usuario_id: uuid.UUID) -> list[ClaveUsuario]:
        return list(self.db.scalars(select(ClaveUsuario).where(
            ClaveUsuario.usuario_id == usuario_id, ClaveUsuario.estado == EstadoClave.ACTIVA).order_by(ClaveUsuario.tipo)))

    def commit(self) -> None:
        self.db.commit()

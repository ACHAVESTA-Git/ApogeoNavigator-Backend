"""Acceso a datos de las claves de usuario: todas las consultas SQL del gestor de claves viven aquí."""
import uuid
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import config as c
from app.db import session as db_session
from app.db.models import Usuario
from app.models.claves import ClaveUsuario, EstadoClave, TipoClave


class ClavesRepository(Protocol):
    def obtener(self, clave_id: uuid.UUID, bloquear: bool = False) -> ClaveUsuario | None: ...
    def usuario(self, usuario_id: uuid.UUID) -> Usuario | None: ...
    def existe_activa(self, usuario_id: uuid.UUID, tipo: TipoClave) -> bool: ...
    def listar(self, usuario_id: uuid.UUID | None, estado: EstadoClave | None, tipo: TipoClave | None,
               nombre_o_hashes: tuple[str, list[str]] | None, limite: int, offset: int) -> tuple[int, list[tuple[ClaveUsuario, Usuario]]]: ...
    def agregar(self, clave: ClaveUsuario) -> None: ...
    def flush(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def marcar_entregadas(self, ids: list[uuid.UUID]) -> None: ...


class SqlClavesRepository:
    """Implementación SQLAlchemy sobre la sesión de la petición."""

    def __init__(self, db: Session):
        self.db = db

    def obtener(self, clave_id: uuid.UUID, bloquear: bool = False) -> ClaveUsuario | None:
        """Con ``bloquear`` toma un bloqueo de fila (SELECT ... FOR UPDATE; SQLite lo ignora)."""
        consulta = select(ClaveUsuario).where(ClaveUsuario.id == clave_id)
        return self.db.scalar(consulta.with_for_update() if bloquear else consulta)

    def usuario(self, usuario_id: uuid.UUID) -> Usuario | None:
        return self.db.get(Usuario, usuario_id)

    def existe_activa(self, usuario_id: uuid.UUID, tipo: TipoClave) -> bool:
        return bool(self.db.scalar(select(ClaveUsuario.id).where(
            ClaveUsuario.usuario_id == usuario_id, ClaveUsuario.tipo == tipo, ClaveUsuario.estado == EstadoClave.ACTIVA)))

    def listar(self, usuario_id, estado, tipo, nombre_o_hashes, limite, offset):
        """``nombre_o_hashes`` es (texto buscado, hashes de email candidatos) o None. Devuelve (total, filas)."""
        consulta = select(ClaveUsuario, Usuario).join(Usuario, Usuario.id == ClaveUsuario.usuario_id)
        if usuario_id:
            consulta = consulta.where(ClaveUsuario.usuario_id == usuario_id)
        if estado:
            consulta = consulta.where(ClaveUsuario.estado == estado)
        if tipo:
            consulta = consulta.where(ClaveUsuario.tipo == tipo)
        if nombre_o_hashes:
            texto, hashes = nombre_o_hashes
            escape = c.LIKE_ESCAPE
            patron = "%" + texto.replace(escape, escape * 2).replace("%", escape + "%").replace("_", escape + "_") + "%"
            consulta = consulta.where(or_(Usuario.email_hash.in_(hashes), Usuario.nombre.ilike(patron, escape=escape)))
        total = self.db.scalar(select(func.count()).select_from(consulta.subquery()))
        filas = self.db.execute(consulta.order_by(ClaveUsuario.creada_utc.desc(), ClaveUsuario.id).limit(limite).offset(offset)).all()
        return total, [(clave, usuario) for clave, usuario in filas]

    def agregar(self, clave: ClaveUsuario) -> None:
        self.db.add(clave)

    def flush(self) -> None:
        self.db.flush()

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def marcar_entregadas(self, ids: list[uuid.UUID]) -> None:
        """Sesión propia: se ejecuta después de enviar la respuesta, cuando la de la petición ya se cerró."""
        with db_session.session_scope() as session:
            for clave in session.scalars(select(ClaveUsuario).where(ClaveUsuario.id.in_(ids))):
                clave.backups = 1

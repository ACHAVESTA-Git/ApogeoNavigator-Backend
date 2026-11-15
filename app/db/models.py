"""Modelo de datos (SQLAlchemy 2.0 tipado). Ver docs/bd/modelo-datos.md."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Enum, ForeignKey, Index, Integer, LargeBinary, String,
                        UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core import config as c
from app.db.base import BIGINT_PK, JSON_TYPE, UTC_DATETIME, Base
from app.db.enums import AccionAuditoria, RolUsuario, TipoCarga


# Un registro sin firma debe declararlo: no hay medias tintas (firma nula solo con algoritmo NINGUNO).
SIGNED_OR_NONE = f"(firma_pqc IS NULL) = (COALESCE(algoritmo_firma, '') = '{c.UNSIGNED_ALGORITHM}')"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (UniqueConstraint("proveedor_oauth", "sub_oauth", name="uq_usuarios_oauth"),
                      CheckConstraint(f"length(email_hash) = {c.HASH_HEX_LENGTH}", name="ck_usuarios_email_hash_longitud"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # El email nunca se guarda en claro (Ley 29733): va cifrado con ML-KEM-1024 (KEM-DEM) y se busca por su HMAC.
    # Usa app/services/usuarios.py (crear_usuario, buscar_por_email, email_de) en lugar de tocar estas columnas.
    email_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    email_hash: Mapped[str] = mapped_column(String(c.HASH_HEX_LENGTH), unique=True)
    nombre: Mapped[str] = mapped_column(String(c.DB_NAME_MAX))
    # Login local: hash Argon2id (nunca la contraseña). Null = sin login local (p. ej. solo OAuth, futuro).
    password_hash: Mapped[str | None] = mapped_column(String(c.DB_PASSWORD_HASH_MAX))
    proveedor_oauth: Mapped[str | None] = mapped_column(String(c.DB_PROVIDER_MAX))
    sub_oauth: Mapped[str | None] = mapped_column(String(c.DB_SUB_MAX))
    rol: Mapped[RolUsuario] = mapped_column(Enum(RolUsuario, name="rol_usuario", values_callable=lambda e: [m.value for m in e]),
                                            default=RolUsuario.ANALISTA)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_utc: Mapped[datetime] = mapped_column(UTC_DATETIME, default=utcnow)
    ultimo_login_utc: Mapped[datetime | None] = mapped_column(UTC_DATETIME)

    sesiones: Mapped[list["Sesion"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")


class Sesion(Base):
    __tablename__ = "sesiones"
    __table_args__ = (Index("ix_sesiones_usuario_id", "usuario_id"), Index("ix_sesiones_expira_utc", "expira_utc"),
                      Index("ix_sesiones_token_hash", "token_hash", unique=True))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    creada_utc: Mapped[datetime] = mapped_column(UTC_DATETIME, default=utcnow)
    expira_utc: Mapped[datetime] = mapped_column(UTC_DATETIME)
    revocada: Mapped[bool] = mapped_column(Boolean, default=False)
    # SHA3-256 del token opaco de la cookie: el token en claro nunca se guarda.
    token_hash: Mapped[str | None] = mapped_column(String(c.HASH_HEX_LENGTH))
    ip_hash: Mapped[str | None] = mapped_column(String(c.HASH_HEX_LENGTH))
    user_agent: Mapped[str | None] = mapped_column(String(c.DB_USER_AGENT_MAX))

    usuario: Mapped[Usuario] = relationship(back_populates="sesiones")


class CargaArchivo(Base):
    __tablename__ = "cargas_archivo"
    __table_args__ = (Index("ix_cargas_archivo_creado_utc", "creado_utc"), Index("ix_cargas_archivo_sha3_256", "sha3_256"),
                      Index("ix_cargas_archivo_usuario_id", "usuario_id"),
                      CheckConstraint(SIGNED_OR_NONE, name=c.CK_SIGNED_OR_NONE.format(tabla="cargas_archivo")),
                      CheckConstraint(f"length(sha3_256) = {c.HASH_HEX_LENGTH}", name="ck_cargas_archivo_sha3_longitud"),
                      CheckConstraint("tamano_bytes >= 0 AND filas >= 0 AND total_errores >= 0", name="ck_cargas_archivo_no_negativos"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(c.DB_ACTOR_MAX))
    tipo: Mapped[TipoCarga] = mapped_column(Enum(TipoCarga, name="tipo_carga", values_callable=lambda e: [m.value for m in e]))
    nombre_archivo: Mapped[str] = mapped_column(String(c.DB_FILENAME_MAX))
    sha3_256: Mapped[str] = mapped_column(String(c.HASH_HEX_LENGTH))
    tamano_bytes: Mapped[int] = mapped_column(BigInteger)
    filas: Mapped[int] = mapped_column(Integer)
    valido: Mapped[bool] = mapped_column(Boolean)
    total_errores: Mapped[int] = mapped_column(Integer)
    ruta_almacenamiento: Mapped[str | None] = mapped_column(String(c.DB_PATH_MAX))
    ruta_historial: Mapped[str | None] = mapped_column(String(c.DB_PATH_MAX))
    creado_utc: Mapped[datetime] = mapped_column(UTC_DATETIME, default=utcnow)
    firma_pqc: Mapped[bytes | None] = mapped_column(LargeBinary)
    algoritmo_firma: Mapped[str | None] = mapped_column(String(c.DB_ALGORITHM_MAX))
    huella_clave: Mapped[str | None] = mapped_column(String(c.DB_KEY_FINGERPRINT_MAX))   # clave pública que firmó


class EventoAuditoria(Base):
    """Evento encadenado: hash_actual = SHA3-256(hash_prev + evento canónico) y firmado con PQC."""
    __tablename__ = "eventos_auditoria"
    __table_args__ = (Index("ix_eventos_auditoria_ts_utc", "ts_utc"), Index("ix_eventos_auditoria_usuario_id", "usuario_id"),
                      Index("ix_eventos_auditoria_accion", "accion"),
                      CheckConstraint(SIGNED_OR_NONE, name=c.CK_SIGNED_OR_NONE.format(tabla="eventos_auditoria")),
                      CheckConstraint(f"length(hash_prev) = {c.HASH_HEX_LENGTH} AND length(hash_actual) = {c.HASH_HEX_LENGTH}",
                                      name="ck_eventos_auditoria_hash_longitud"),
                      CheckConstraint("secuencia >= 1", name="ck_eventos_auditoria_secuencia"))

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    ts_utc: Mapped[datetime] = mapped_column(UTC_DATETIME)
    secuencia: Mapped[int] = mapped_column(BigInteger, unique=True)   # 1, 2, 3...: entra en el hash y la firma
    # Sin FK a propósito: el registro de auditoría no puede cambiar si se toca un usuario (se desactivan, no se borran).
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor: Mapped[str] = mapped_column(String(c.DB_ACTOR_MAX))
    accion: Mapped[AccionAuditoria] = mapped_column(Enum(AccionAuditoria, name="accion_auditoria",
                                                         values_callable=lambda e: [m.value for m in e]))
    recurso: Mapped[str | None] = mapped_column(String(c.DB_RESOURCE_MAX))
    detalle: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    ip_hash: Mapped[str | None] = mapped_column(String(c.HASH_HEX_LENGTH))
    user_agent: Mapped[str | None] = mapped_column(String(c.DB_USER_AGENT_MAX))
    hash_prev: Mapped[str] = mapped_column(String(c.HASH_HEX_LENGTH), unique=True)   # único: impide bifurcar la cadena
    hash_actual: Mapped[str] = mapped_column(String(c.HASH_HEX_LENGTH), unique=True)
    firma_pqc: Mapped[bytes | None] = mapped_column(LargeBinary)
    algoritmo_firma: Mapped[str | None] = mapped_column(String(c.DB_ALGORITHM_MAX))
    huella_clave: Mapped[str | None] = mapped_column(String(c.DB_KEY_FINGERPRINT_MAX))

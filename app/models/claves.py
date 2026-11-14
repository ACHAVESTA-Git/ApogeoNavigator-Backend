"""Claves públicas por usuario (gestor de claves PQC). NUNCA se guarda una clave privada.

Cada usuario puede tener, como máximo, una clave ACTIVA por tipo: ML-KEM-1024 («kem», para recibir datos
cifrados) y ML-DSA-65 («firma»). Las revocadas se conservan: sirven para verificar lo firmado antes.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Integer, LargeBinary, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import UTC_DATETIME, Base
from app.db.models import utcnow

ALGORITHM_MAX = 20
FINGERPRINT_LENGTH = 64            # SHA3-256 en hexadecimal
MOTIVE_MAX = 200
ACTIVE_FILTER = "estado = 'activa'"


class TipoClave(enum.StrEnum):
    KEM = "kem"
    FIRMA = "firma"


class EstadoClave(enum.StrEnum):
    ACTIVA = "activa"
    REVOCADA = "revocada"


def _values(enum_class):
    return [member.value for member in enum_class]


class ClaveUsuario(Base):
    __tablename__ = "claves_usuario"
    __table_args__ = (
        Index("ix_claves_usuario_usuario_id", "usuario_id"),
        # Una sola clave activa por usuario y tipo (también ante dos peticiones simultáneas).
        Index("ux_claves_usuario_activa", "usuario_id", "tipo", unique=True,
              sqlite_where=text(ACTIVE_FILTER), postgresql_where=text(ACTIVE_FILTER)),
        CheckConstraint("backups IN (0, 1)", name="ck_claves_usuario_backups"),
        CheckConstraint("(estado = 'activa') = (revocada_utc IS NULL)", name="ck_claves_usuario_revocacion"),
        CheckConstraint(f"length(huella_sha3_256) = {FINGERPRINT_LENGTH}", name="ck_claves_usuario_huella"),
        CheckConstraint("tamano_publica > 0 AND tamano_privada > 0", name="ck_claves_usuario_tamanos"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # RESTRICT: los usuarios se desactivan, no se borran; así no se pierde la verificación de lo firmado.
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    tipo: Mapped[TipoClave] = mapped_column(Enum(TipoClave, name="tipo_clave", values_callable=_values))
    algoritmo: Mapped[str] = mapped_column(String(ALGORITHM_MAX))
    clave_publica: Mapped[bytes] = mapped_column(LargeBinary)
    huella_sha3_256: Mapped[str] = mapped_column(String(FINGERPRINT_LENGTH), unique=True)
    tamano_publica: Mapped[int] = mapped_column(Integer)
    tamano_privada: Mapped[int] = mapped_column(Integer)      # bytes de la privada entregada (semilla), no se guarda
    estado: Mapped[EstadoClave] = mapped_column(Enum(EstadoClave, name="estado_clave", values_callable=_values),
                                                default=EstadoClave.ACTIVA)
    creada_utc: Mapped[datetime] = mapped_column(UTC_DATETIME, default=utcnow)
    revocada_utc: Mapped[datetime | None] = mapped_column(UTC_DATETIME)
    motivo_revocacion: Mapped[str | None] = mapped_column(String(MOTIVE_MAX))
    reemplazada_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claves_usuario.id", ondelete="RESTRICT"))
    # Copias de la privada entregadas: 0 (aún no entregada) o 1 (entregada una sola vez). Nunca más de una.
    backups: Mapped[int] = mapped_column(Integer, default=0)

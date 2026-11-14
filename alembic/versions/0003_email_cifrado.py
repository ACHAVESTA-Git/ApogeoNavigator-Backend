"""Email de usuarios cifrado en reposo: columnas nuevas, copia cifrada y verificación (fase 1 de 2).

Añade `email_cifrado` (BYTEA) y `email_hash` (HMAC-SHA256 único, para búsquedas exactas), cifra los emails existentes con
las claves del servidor (PQC_KEM_PRIVATE_KEY y EMAIL_HASH_KEY, obligatorias si hay filas) y comprueba, fila a fila, que
descifrar el blob devuelve el email original. La columna `email` en claro se conserva hasta la migración 0004.

Si faltan PQC_KEM_PRIVATE_KEY o EMAIL_HASH_KEY y hay usuarios, aborta antes de tocar nada (ver `_require_server_keys`).
Atomicidad: en PostgreSQL el DDL es transaccional y un fallo a mitad revierte todo. En SQLite (solo pruebas) no: una
migración fallida a mitad puede dejar las columnas añadidas; restaura la copia y vuelve a empezar.

Revision ID: 0003_email_cifrado
Revises: 0002_claves
Create Date: 2026-11-14
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0003_email_cifrado"
down_revision = "0002_claves"
branch_labels = None
depends_on = None

BATCH = 500

SERVER_KEYS = ("PQC_KEM_PRIVATE_KEY", "EMAIL_HASH_KEY")


def _require_server_keys(bind, reason: str) -> None:
    """Aborta ANTES de tocar nada si hay usuarios y falta alguna clave del servidor.

    Sin ellas, el gestor generaría claves de DESARROLLO al vuelo: se cifraría con una clave que nadie conserva y los emails
    quedarían irrecuperables. Se exige que las variables estén definidas explícitamente, también fuera de producción.
    """
    if not bind.execute(sa.select(sa.func.count()).select_from(_usuarios())).scalar():
        return
    missing = [name for name in SERVER_KEYS if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Hay usuarios y faltan {', '.join(missing)}: {reason}. No se ha modificado nada.")


def _usuarios() -> sa.Table:
    return sa.table("usuarios", sa.column("id", sa.Uuid()), sa.column("email", sa.String()),
                    sa.column("email_cifrado", sa.LargeBinary()), sa.column("email_hash", sa.String()))


def upgrade() -> None:
    _require_server_keys(op.get_bind(), "cifrar los emails existentes con claves de desarrollo los dejaría irrecuperables")
    op.add_column("usuarios", sa.Column("email_cifrado", sa.LargeBinary(), nullable=True))
    op.add_column("usuarios", sa.Column("email_hash", sa.String(64), nullable=True))
    with op.batch_alter_table("usuarios") as batch:      # batch: SQLite no admite ALTER de restricciones
        batch.create_unique_constraint("uq_usuarios_email_hash", ["email_hash"])

    bind, usuarios = op.get_bind(), _usuarios()
    ids = [r[0] for r in bind.execute(sa.select(usuarios.c.id))]       # solo los ids en memoria; el resto, por lotes
    if not ids:
        return
    from app.security import gestor_claves as gk          # importación perezosa: solo hace falta con datos

    for start in range(0, len(ids), BATCH):
        for uid, email in bind.execute(sa.select(usuarios.c.id, usuarios.c.email).where(usuarios.c.id.in_(ids[start:start + BATCH]))).all():
            blob = gk.cifrar_email(email)
            if gk.descifrar_email(blob) != email.strip().lower():
                raise RuntimeError(f"El cifrado del usuario {uid} no se pudo verificar; no se continúa")
            bind.execute(usuarios.update().where(usuarios.c.id == uid).values(email_cifrado=blob, email_hash=gk.email_hash(email)))
    pendientes = bind.execute(sa.select(sa.func.count()).select_from(usuarios).where(usuarios.c.email_cifrado.is_(None))).scalar()
    if pendientes:
        raise RuntimeError(f"{pendientes} usuarios sin email cifrado")


def downgrade() -> None:
    with op.batch_alter_table("usuarios") as batch:
        batch.drop_constraint("uq_usuarios_email_hash", type_="unique")
        batch.drop_column("email_hash")
        batch.drop_column("email_cifrado")

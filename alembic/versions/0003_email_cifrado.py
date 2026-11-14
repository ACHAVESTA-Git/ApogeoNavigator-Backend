"""Email de usuarios cifrado en reposo: columnas nuevas, copia cifrada y verificación (fase 1 de 2).

Añade `email_cifrado` (BYTEA) y `email_hash` (HMAC-SHA256 único, para búsquedas exactas), cifra los emails existentes con
las claves del servidor (PQC_KEM_PRIVATE_KEY y EMAIL_HASH_KEY, obligatorias si hay filas) y comprueba, fila a fila, que
descifrar el blob devuelve el email original. La columna `email` en claro se conserva hasta la migración 0004.

Revision ID: 0003_email_cifrado
Revises: 0002_claves
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_email_cifrado"
down_revision = "0002_claves"
branch_labels = None
depends_on = None

BATCH = 500


def _usuarios() -> sa.Table:
    return sa.table("usuarios", sa.column("id", sa.Uuid()), sa.column("email", sa.String()),
                    sa.column("email_cifrado", sa.LargeBinary()), sa.column("email_hash", sa.String()))


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("email_cifrado", sa.LargeBinary(), nullable=True))
    op.add_column("usuarios", sa.Column("email_hash", sa.String(64), nullable=True))
    with op.batch_alter_table("usuarios") as batch:      # batch: SQLite no admite ALTER de restricciones
        batch.create_unique_constraint("uq_usuarios_email_hash", ["email_hash"])

    bind, usuarios = op.get_bind(), _usuarios()
    rows = bind.execute(sa.select(usuarios.c.id, usuarios.c.email)).all()
    if not rows:
        return
    from app.security import gestor_claves as gk          # importación perezosa: solo hace falta con datos

    for start in range(0, len(rows), BATCH):
        for uid, email in rows[start:start + BATCH]:
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

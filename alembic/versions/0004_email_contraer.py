"""Email de usuarios cifrado en reposo: quitar el email en claro (fase 2 de 2).

Antes de borrar `email` comprueba, fila a fila, que `descifrar_email(email_cifrado)` coincide con el email en claro y que
`email_hash` es el HMAC actual o uno de los anteriores. Si algo no cuadra, aborta sin tocar nada. Después deja
`email_cifrado` y `email_hash` como NOT NULL, con CHECK de longitud, y elimina `email`.
El downgrade vuelve a crear `email` descifrando los blobs (hacen falta las mismas claves). Aviso: el email restituido
es el normalizado (minúsculas y sin espacios), no necesariamente el texto original; el cifrado solo guardó esa forma.

Revision ID: 0004_email_contraer
Revises: 0003_email_cifrado
Create Date: 2026-11-14
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0004_email_contraer"
down_revision = "0003_email_cifrado"
branch_labels = None
depends_on = None


def _usuarios() -> sa.Table:
    return sa.table("usuarios", sa.column("id", sa.Uuid()), sa.column("email", sa.String()),
                    sa.column("email_cifrado", sa.LargeBinary()), sa.column("email_hash", sa.String()))


SERVER_KEYS = ("PQC_KEM_PRIVATE_KEY", "EMAIL_HASH_KEY")


def _require_server_keys(bind, reason: str) -> None:
    """Aborta ANTES de tocar nada si hay usuarios y falta alguna clave del servidor (ver la migración 0003)."""
    if not bind.execute(sa.select(sa.func.count()).select_from(_usuarios())).scalar():
        return
    missing = [name for name in SERVER_KEYS if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Hay usuarios y faltan {', '.join(missing)}: {reason}. No se ha modificado nada.")


def upgrade() -> None:
    bind, usuarios = op.get_bind(), _usuarios()
    _require_server_keys(bind, "no se puede verificar el cifrado antes de borrar el email en claro")
    rows = bind.execute(sa.select(usuarios.c.id, usuarios.c.email, usuarios.c.email_cifrado, usuarios.c.email_hash)).all()
    if rows:
        from app.security import gestor_claves as gk
        for uid, email, blob, email_hash in rows:
            if blob is None or email_hash is None:
                raise RuntimeError(f"El usuario {uid} no tiene email cifrado: no se borra el email en claro")
            if gk.descifrar_email(blob) != email.strip().lower():
                raise RuntimeError(f"El email cifrado del usuario {uid} no coincide con el original: no se borra el email")
            if email_hash not in gk.email_hash_candidatos(email):
                raise RuntimeError(f"El hash del email del usuario {uid} no coincide: no se borra el email en claro")
    with op.batch_alter_table("usuarios") as batch:
        batch.alter_column("email_cifrado", existing_type=sa.LargeBinary(), nullable=False)
        batch.alter_column("email_hash", existing_type=sa.String(64), nullable=False)
        batch.create_check_constraint("ck_usuarios_email_hash_longitud", "length(email_hash) = 64")
        batch.drop_column("email")


def downgrade() -> None:
    bind, usuarios = op.get_bind(), _usuarios()
    _require_server_keys(bind, "no se pueden descifrar los emails para restituirlos")
    op.add_column("usuarios", sa.Column("email", sa.String(320), nullable=True))
    rows = bind.execute(sa.select(usuarios.c.id, usuarios.c.email_cifrado)).all()
    if rows:
        from app.security import gestor_claves as gk
        for uid, blob in rows:
            bind.execute(usuarios.update().where(usuarios.c.id == uid).values(email=gk.descifrar_email(blob)))
    with op.batch_alter_table("usuarios") as batch:
        batch.alter_column("email", existing_type=sa.String(320), nullable=False)
        batch.create_unique_constraint("uq_usuarios_email", ["email"])
        batch.drop_constraint("ck_usuarios_email_hash_longitud", type_="check")
        batch.alter_column("email_cifrado", existing_type=sa.LargeBinary(), nullable=True)
        batch.alter_column("email_hash", existing_type=sa.String(64), nullable=True)

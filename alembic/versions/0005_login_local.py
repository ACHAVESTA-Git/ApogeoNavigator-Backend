"""Login local: hash de contraseña en usuarios y hash del token en sesiones.

- `usuarios.password_hash`: hash Argon2id (nulo = sin login local). Nunca se guarda la contraseña.
- `sesiones.token_hash`: SHA3-256 del token opaco de la cookie, con índice único. Nunca se guarda el token.
Las dos columnas son nulas para no romper filas existentes. Aditiva y reversible.

Revision ID: 0005_login_local
Revises: 0004_email_contraer
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_login_local"
down_revision = "0004_email_contraer"
branch_labels = None
depends_on = None

PASSWORD_HASH_MAX = 255
TOKEN_HASH_LENGTH = 64
TOKEN_INDEX = "ix_sesiones_token_hash"


def upgrade() -> None:
    with op.batch_alter_table("usuarios") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(PASSWORD_HASH_MAX), nullable=True))
    with op.batch_alter_table("sesiones") as batch:
        batch.add_column(sa.Column("token_hash", sa.String(TOKEN_HASH_LENGTH), nullable=True))
        batch.create_index(TOKEN_INDEX, ["token_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("sesiones") as batch:
        batch.drop_index(TOKEN_INDEX)
        batch.drop_column("token_hash")
    with op.batch_alter_table("usuarios") as batch:
        batch.drop_column("password_hash")

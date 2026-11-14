"""Claves públicas por usuario (gestor de claves PQC). No guarda claves privadas.

Revision ID: 0002_claves
Revises: 0001
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_claves"
down_revision = "0001"
branch_labels = None
depends_on = None

UTC = sa.DateTime(timezone=True)
TIPOS = ("kem", "firma")
ESTADOS = ("activa", "revocada")
ACTIVE_FILTER = "estado = 'activa'"


def upgrade() -> None:
    op.create_table(
        "claves_usuario",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tipo", sa.Enum(*TIPOS, name="tipo_clave"), nullable=False),
        sa.Column("algoritmo", sa.String(20), nullable=False),
        sa.Column("clave_publica", sa.LargeBinary(), nullable=False),
        sa.Column("huella_sha3_256", sa.String(64), nullable=False, unique=True),
        sa.Column("tamano_publica", sa.Integer(), nullable=False),
        sa.Column("tamano_privada", sa.Integer(), nullable=False),
        sa.Column("estado", sa.Enum(*ESTADOS, name="estado_clave"), nullable=False),
        sa.Column("creada_utc", UTC, nullable=False),
        sa.Column("revocada_utc", UTC),
        sa.Column("motivo_revocacion", sa.String(200)),
        sa.Column("reemplazada_por", sa.Uuid(), sa.ForeignKey("claves_usuario.id", ondelete="RESTRICT")),
        sa.Column("backups", sa.Integer(), nullable=False),
        sa.CheckConstraint("backups IN (0, 1)", name="ck_claves_usuario_backups"),
        sa.CheckConstraint("(estado = 'activa') = (revocada_utc IS NULL)", name="ck_claves_usuario_revocacion"),
        sa.CheckConstraint("length(huella_sha3_256) = 64", name="ck_claves_usuario_huella"),
        sa.CheckConstraint("tamano_publica > 0 AND tamano_privada > 0", name="ck_claves_usuario_tamanos"),
    )
    op.create_index("ix_claves_usuario_usuario_id", "claves_usuario", ["usuario_id"])
    op.create_index("ux_claves_usuario_activa", "claves_usuario", ["usuario_id", "tipo"], unique=True,
                    sqlite_where=sa.text(ACTIVE_FILTER), postgresql_where=sa.text(ACTIVE_FILTER))


def downgrade() -> None:
    op.drop_table("claves_usuario")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in ("estado_clave", "tipo_clave"):
            sa.Enum(name=name).drop(bind, checkfirst=True)

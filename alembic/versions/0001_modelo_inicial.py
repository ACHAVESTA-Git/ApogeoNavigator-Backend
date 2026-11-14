"""Modelo inicial: usuarios, sesiones, cargas_archivo y eventos_auditoria.

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UTC = sa.DateTime(timezone=True)
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

ROLES = ("analista", "revisor", "admin")
TIPOS = ("sites", "efemerides", "metricas")
ACCIONES = ("LOGIN", "LOGIN_FALLIDO", "LOGOUT", "CARGA_VALIDADA", "CARGA_GUARDADA", "CARGA_RECHAZADA",
            "CAMBIO_ROL", "CAMBIO_DATOS")


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("proveedor_oauth", sa.String(40)),
        sa.Column("sub_oauth", sa.String(255)),
        sa.Column("rol", sa.Enum(*ROLES, name="rol_usuario"), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("creado_utc", UTC, nullable=False),
        sa.Column("ultimo_login_utc", UTC),
        sa.UniqueConstraint("proveedor_oauth", "sub_oauth", name="uq_usuarios_oauth"),
    )
    op.create_table(
        "sesiones",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("creada_utc", UTC, nullable=False),
        sa.Column("expira_utc", UTC, nullable=False),
        sa.Column("revocada", sa.Boolean(), nullable=False),
        sa.Column("ip_hash", sa.String(64)),
        sa.Column("user_agent", sa.String(200)),
    )
    op.create_index("ix_sesiones_usuario_id", "sesiones", ["usuario_id"])
    op.create_index("ix_sesiones_expira_utc", "sesiones", ["expira_utc"])
    op.create_table(
        "cargas_archivo",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("tipo", sa.Enum(*TIPOS, name="tipo_carga"), nullable=False),
        sa.Column("nombre_archivo", sa.String(120), nullable=False),
        sa.Column("sha3_256", sa.String(64), nullable=False),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=False),
        sa.Column("filas", sa.Integer(), nullable=False),
        sa.Column("valido", sa.Boolean(), nullable=False),
        sa.Column("total_errores", sa.Integer(), nullable=False),
        sa.Column("ruta_almacenamiento", sa.String(500)),
        sa.Column("ruta_historial", sa.String(500)),
        sa.Column("creado_utc", UTC, nullable=False),
        sa.Column("firma_pqc", sa.LargeBinary()),
        sa.Column("algoritmo_firma", sa.String(40)),
    )
    op.create_index("ix_cargas_archivo_creado_utc", "cargas_archivo", ["creado_utc"])
    op.create_index("ix_cargas_archivo_sha3_256", "cargas_archivo", ["sha3_256"])
    op.create_index("ix_cargas_archivo_usuario_id", "cargas_archivo", ["usuario_id"])
    op.create_table(
        "eventos_auditoria",
        sa.Column("id", BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("ts_utc", UTC, nullable=False),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("accion", sa.Enum(*ACCIONES, name="accion_auditoria"), nullable=False),
        sa.Column("recurso", sa.String(200)),
        sa.Column("detalle", JSON_TYPE, nullable=False),
        sa.Column("ip_hash", sa.String(64)),
        sa.Column("user_agent", sa.String(200)),
        sa.Column("hash_prev", sa.String(64), nullable=False, unique=True),
        sa.Column("hash_actual", sa.String(64), nullable=False, unique=True),
        sa.Column("firma_pqc", sa.LargeBinary()),
        sa.Column("algoritmo_firma", sa.String(40)),
    )
    op.create_index("ix_eventos_auditoria_ts_utc", "eventos_auditoria", ["ts_utc"])
    op.create_index("ix_eventos_auditoria_usuario_id", "eventos_auditoria", ["usuario_id"])
    op.create_index("ix_eventos_auditoria_accion", "eventos_auditoria", ["accion"])


def downgrade() -> None:
    op.drop_table("eventos_auditoria")
    op.drop_table("cargas_archivo")
    op.drop_table("sesiones")
    op.drop_table("usuarios")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in ("accion_auditoria", "tipo_carga", "rol_usuario"):
            sa.Enum(name=name).drop(bind, checkfirst=True)

"""Auditoría de la consulta de la proyección: nueva acción VER_PROYECCION.

Solo PostgreSQL necesita cambio: `accion_auditoria` es un tipo ENUM nativo y hay que añadirle el valor. En SQLite (pruebas)
la columna es texto sin restricción. Un valor de un ENUM de PostgreSQL no se puede quitar, así que el downgrade no hace
nada: dejar el valor sin usar es inocuo y las filas que lo usen siguen siendo válidas.

Revision ID: 0006_auditoria_proyeccion
Revises: 0005_login_local
Create Date: 2026-11-15
"""
from alembic import op

revision = "0006_auditoria_proyeccion"
down_revision = "0005_login_local"
branch_labels = None
depends_on = None

NUEVA_ACCION = "VER_PROYECCION"      # 14 caracteres: no ensancha la columna de texto (VARCHAR(15)) de SQLite


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE accion_auditoria ADD VALUE IF NOT EXISTS '{NUEVA_ACCION}'")


def downgrade() -> None:
    """Sin cambios: PostgreSQL no permite quitar un valor de un ENUM."""

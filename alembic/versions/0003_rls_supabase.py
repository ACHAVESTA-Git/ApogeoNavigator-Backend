"""RLS sin políticas y sin permisos para anon/authenticated (Supabase).

Supabase expone por su API REST (PostgREST) las tablas del esquema public con los roles `anon` y `authenticated`.
Nuestras tablas solo las usa el backend, que se conecta con el rol propietario (que ignora RLS). Esta migración:
  1. activa Row Level Security en todas las tablas, sin políticas: quien no sea propietario no ve nada;
  2. retira todos los permisos a anon y authenticated sobre tablas y secuencias, y sobre las que se creen después.
No hace nada en SQLite ni, salvo el punto 1, en PostgreSQL sin esos roles (local, Cloud SQL): el REVOKE solo se aplica
a los roles que existan. No se usa FORCE ROW LEVEL SECURITY: bloquearía al propietario, que es el backend.

Revision ID: 0003_rls
Revises: 0002_claves
Create Date: 2026-11-14
"""
from alembic import op

revision = "0003_rls"
down_revision = "0002_claves"
branch_labels = None
depends_on = None

TABLES = ("usuarios", "sesiones", "cargas_archivo", "eventos_auditoria", "claves_usuario")
SUPABASE_ROLES = ("anon", "authenticated")


def _only_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _only_postgres():
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for role in SUPABASE_ROLES:
        tables = ", ".join(TABLES)
        op.execute(f"""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL ON TABLE {tables} FROM {role};
                    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role};
                    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role};
                    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role};
                END IF;
            END $$
        """)


def downgrade() -> None:
    """Desactiva RLS. Los permisos retirados a anon y authenticated NO se restituyen (era una exposición no deseada)."""
    if not _only_postgres():
        return
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

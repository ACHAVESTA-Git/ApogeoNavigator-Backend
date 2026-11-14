"""Pruebas contra PostgreSQL real (trigger de inmutabilidad, CHECK, bloqueo consultivo, migración).

Se omiten sin TEST_DATABASE_URL. La base debe ser desechable (su nombre debe contener «test»): cada prueba
borra y recrea el esquema `public`. Ejemplo con docker compose:
    docker compose up -d postgres
    docker compose exec postgres psql -U apogeo -d postgres -c "CREATE DATABASE apogeo_test"
    TEST_DATABASE_URL=postgresql+psycopg://apogeo:apogeo_dev@localhost:5432/apogeo_test pytest tests/test_postgres.py
"""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import session as db_session
from app.db.base import Base
from app.db.enums import AccionAuditoria as A
from app.utils import auditoria_util as au

try:    # modelos de otras migraciones (gestor de claves), si existen, para que la comparación sea completa
    import app.models.claves  # noqa: F401
except ImportError:
    pass

URL = os.getenv("TEST_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not URL, reason="sin TEST_DATABASE_URL")


@pytest.fixture
def pg(monkeypatch, pqc_keys):
    from app.security import pqc
    assert "test" in URL.rsplit("/", 1)[-1], "TEST_DATABASE_URL debe apuntar a una base desechable (nombre con «test»)"
    monkeypatch.setenv("PQC_PRIVATE_KEY", pqc_keys[0])
    monkeypatch.setenv("PQC_PUBLIC_KEY", pqc_keys[1])
    monkeypatch.setenv("DATABASE_URL", URL)
    pqc.reiniciar()
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", URL)
    command.upgrade(cfg, "head")
    db_session.configure(None)
    au.set_firmante()
    yield engine, cfg
    db_session.configure(None)
    au.set_firmante()
    pqc.reiniciar()
    engine.dispose()


def test_migracion_sube_y_baja_sin_dejar_restos(pg):
    engine, cfg = pg
    assert {"usuarios", "sesiones", "cargas_archivo", "eventos_auditoria"} <= set(inspect(engine).get_table_names())
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
        assert conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname = 'eventos_auditoria_inmutable'")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM pg_type WHERE typname IN "
                                 "('rol_usuario', 'tipo_carga', 'accion_auditoria')")).scalar() == 0


def test_tipos_reales_de_postgres(pg):
    engine, _ = pg
    cols = {c["name"]: str(c["type"]) for c in inspect(engine).get_columns("eventos_auditoria")}
    assert cols["detalle"] == "JSONB" and cols["firma_pqc"] == "BYTEA" and cols["id"] == "BIGINT"


def test_la_migracion_coincide_con_los_modelos_en_postgres(pg):
    engine, _ = pg
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff


def _un_evento():
    db_session.configure(create_engine(URL))
    return au.registrar_evento(A.LOGIN, "ana", detalle={"i": 1}, estricto=True)


@pytest.mark.parametrize("sql", [
    "UPDATE eventos_auditoria SET actor = 'intruso'",
    "DELETE FROM eventos_auditoria",
    "TRUNCATE eventos_auditoria",
])
def test_el_trigger_impide_modificar_borrar_y_vaciar(pg, sql):
    engine, _ = pg
    _un_evento()
    with pytest.raises(DBAPIError, match="solo inserción"):
        with engine.begin() as conn:
            conn.execute(text(sql))
    assert au.verificar_cadena().valida and au.verificar_cadena().n_eventos == 1


def test_borrar_un_usuario_no_toca_la_auditoria_en_postgres(pg):
    import uuid
    engine, _ = pg
    uid = uuid.uuid4()
    db_session.configure(create_engine(URL))
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO usuarios (id, email_cifrado, email_hash, nombre, rol, activo, creado_utc) "
                          "VALUES (:i, 'x'::bytea, repeat('a', 64), 'Ana', 'analista', true, now())"), {"i": uid})
    au.registrar_evento(A.LOGIN, "ana", usuario_id=uid, estricto=True)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM usuarios WHERE id = :i"), {"i": uid})       # no debe disparar el trigger
    assert au.verificar_cadena().valida


def test_check_de_firma_en_postgres(pg):
    engine, _ = pg
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO eventos_auditoria (secuencia, ts_utc, actor, accion, detalle, hash_prev, hash_actual) "
                              "VALUES (1, now(), 'x', 'LOGIN', '{}', repeat('0', 64), repeat('a', 64))"))


def test_escritores_concurrentes_no_pierden_ni_bifurcan_la_cadena(pg):
    db_session.configure(create_engine(URL, pool_size=10, max_overflow=10))
    with ThreadPoolExecutor(16) as pool:
        resultados = list(pool.map(lambda i: au.registrar_evento(A.LOGIN, f"u{i}", detalle={"i": i}, estricto=True), range(40)))
    assert all(r is not None for r in resultados)
    r = au.verificar_cadena()
    assert r.valida and r.n_eventos == 40 and r.ultima_secuencia == 40


def test_el_primer_evento_con_la_tabla_vacia_tambien_se_serializa(pg):
    db_session.configure(create_engine(URL, pool_size=10, max_overflow=10))
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: au.registrar_evento(A.LOGIN, f"u{i}", estricto=True), range(8)))
    r = au.verificar_cadena()
    assert r.valida and r.n_eventos == 8 and r.primer_hash_prev_es_genesis is True


# ------------------------------------------------------------------ email cifrado (migraciones 0004 y 0005)
def test_migracion_del_email_cifrado_con_datos_en_postgres(pg, monkeypatch):
    import uuid
    from app.core import config
    from app.security import gestor_claves as gk
    semilla, clave_hash = gk.generar_claves_servidor()
    monkeypatch.setenv(config.ENV_KEM_PRIVATE, semilla)
    monkeypatch.setenv(config.ENV_EMAIL_HASH_KEY, clave_hash)
    gk.reiniciar()
    engine, cfg = pg
    command.downgrade(cfg, "0003_rls")
    with engine.begin() as conn:
        for email in ("Ana@Example.org", "luis@example.org"):
            conn.execute(text("INSERT INTO usuarios (id, email, nombre, rol, activo, creado_utc) "
                              "VALUES (:i, :e, 'Ana', 'analista', true, now())"), {"i": uuid.uuid4(), "e": email})
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        columnas = {c["name"]: str(c["type"]) for c in inspect(conn).get_columns("usuarios")}
        assert "email" not in columnas and columnas["email_cifrado"] == "BYTEA"
        filas = conn.execute(text("SELECT email_cifrado, email_hash FROM usuarios")).fetchall()
        assert sorted(gk.descifrar_email(bytes(f[0])) for f in filas) == ["ana@example.org", "luis@example.org"]
        assert conn.execute(text("SELECT count(*) FROM pg_constraint WHERE conname = 'uq_usuarios_email_hash'")).scalar() == 1
    with pytest.raises(IntegrityError):                                       # el hash sigue siendo único
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO usuarios (id, email_cifrado, email_hash, nombre, rol, activo, creado_utc) "
                              "SELECT gen_random_uuid(), email_cifrado, email_hash, 'Copia', 'analista', true, now() FROM usuarios LIMIT 1"))
    command.downgrade(cfg, "0003_rls")
    with engine.connect() as conn:
        assert sorted(r[0] for r in conn.execute(text("SELECT email FROM usuarios"))) == ["ana@example.org", "luis@example.org"]
    gk.reiniciar()

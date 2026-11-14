"""Opciones del motor según el destino: PostgreSQL, Supabase (pooler en modo transacción) y SQLite."""
import pytest
from sqlalchemy import create_engine

from app.core import config as c
from app.db import session as db_session

LOCAL = "postgresql+psycopg://apogeo:x@postgres:5432/apogeo"
SUPABASE_POOLER = "postgresql+psycopg://postgres.abcd:x@aws-0-eu.pooler.supabase.com:6543/postgres?sslmode=require"
SUPABASE_DIRECTO = "postgresql+psycopg://postgres:x@db.abcd.supabase.co:5432/postgres?sslmode=require"
CLOUD_SQL = "postgresql+psycopg://app:x@/apogeo?host=/cloudsql/proy:region:inst"


@pytest.mark.parametrize("url", [LOCAL, SUPABASE_POOLER, SUPABASE_DIRECTO, CLOUD_SQL])
def test_psycopg_desactiva_las_sentencias_preparadas(url):
    assert db_session.engine_options(url)["connect_args"] == {"prepare_threshold": None}


@pytest.mark.parametrize("url", [LOCAL, SUPABASE_POOLER, CLOUD_SQL])
def test_pool_pequeno_con_pre_ping_en_postgresql(url):
    o = db_session.engine_options(url)
    assert o["pool_pre_ping"] is True and o["pool_size"] == c.DB_POOL_SIZE and o["max_overflow"] == c.DB_MAX_OVERFLOW
    assert o["pool_recycle"] == c.DB_POOL_RECYCLE_SECONDS and o["pool_timeout"] == c.DB_POOL_TIMEOUT_SECONDS


def test_sqlite_no_recibe_parametros_de_pool_ni_de_psycopg():
    assert db_session.engine_options("sqlite://") == {"pool_pre_ping": True}


@pytest.mark.parametrize("url", [LOCAL, SUPABASE_POOLER, SUPABASE_DIRECTO, CLOUD_SQL])
def test_las_urls_se_aceptan_sin_conectar(url):
    engine = create_engine(url, **db_session.engine_options(url))      # create_engine no abre conexiones
    assert engine.dialect.driver == "psycopg"


def test_sslmode_viaja_en_la_url():
    assert create_engine(SUPABASE_POOLER).url.query["sslmode"] == "require"


def test_get_engine_usa_las_opciones(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", SUPABASE_POOLER)
    db_session.configure(None)
    engine = db_session.get_engine()
    try:
        assert engine.pool.size() == c.DB_POOL_SIZE
    finally:
        db_session.configure(None)

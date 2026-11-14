"""Motor y sesiones de SQLAlchemy. Sin DATABASE_URL la base de datos queda desactivada."""
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import config

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def configure(engine: Engine | None) -> None:
    """Fija (o borra) el motor global. Lo usan las pruebas y el arranque."""
    global _engine, _factory
    _engine = engine
    _factory = sessionmaker(engine, expire_on_commit=False) if engine is not None else None


def get_engine() -> Engine | None:
    if _engine is None and config.get_database_url():
        url = config.get_database_url()
        options: dict = {"pool_pre_ping": True}
        if not url.startswith(config.DB_SQLITE_PREFIX):     # SQLite no admite estos parámetros de pool
            options |= {"pool_size": config.DB_POOL_SIZE, "max_overflow": config.DB_MAX_OVERFLOW,
                        "pool_recycle": config.DB_POOL_RECYCLE_SECONDS, "pool_timeout": config.DB_POOL_TIMEOUT_SECONDS}
        configure(create_engine(url, **options))
    return _engine


def db_enabled() -> bool:
    return get_engine() is not None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión con commit al salir y rollback ante error."""
    get_engine()
    if _factory is None:
        raise RuntimeError(config.MSG_DB_DISABLED)
    session = _factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI: 503 si no hay base de datos."""
    if not db_enabled():
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_DB_DISABLED)
    with session_scope() as session:
        yield session

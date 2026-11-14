from sqlalchemy import BigInteger, DateTime, Integer, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# Tipos portables: PostgreSQL usa JSONB y BIGSERIAL; SQLite (pruebas) usa JSON e INTEGER.
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
BIGINT_PK = BigInteger().with_variant(Integer(), "sqlite")
UTC_DATETIME = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass

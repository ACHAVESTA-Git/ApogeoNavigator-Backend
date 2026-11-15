"""Dependencias de FastAPI: construyen los servicios con sus repositorios. Es el único sitio que conoce las implementaciones."""
from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.claves import SqlClavesRepository
from app.services.carga import CargaService, CargaServiceImpl
from app.services.claves import ClavesService, ClavesServiceImpl
from app.services.claves.auditor import AuditorSql


def get_claves_service(db: Session = Depends(get_db)) -> ClavesService:
    return ClavesServiceImpl(SqlClavesRepository(db), AuditorSql(db))


def get_carga_service() -> CargaService:
    return CargaServiceImpl()

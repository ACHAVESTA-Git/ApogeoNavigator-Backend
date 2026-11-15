from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from app.core import config
from app.core.cors import add_cors
from app.core.limits import upload_guard
from app.db.session import db_enabled
from app.routers import admin_claves, auditoria, datos, lectura, seguridad, simulacion
from app.schemas.sites import Site
from app.services import datastore, storage

@asynccontextmanager
async def lifespan(_: FastAPI):
    """En producción, con base de datos, la sal del HMAC de la IP es obligatoria."""
    if config.is_production() and db_enabled() and not config.get_ip_hash_salt():
        raise RuntimeError(config.MSG_AUDIT_SALT_REQUIRED)
    datastore.startup()          # producción exige DATA_BUCKET; descarga o siembra los datos
    yield


app = FastAPI(title=config.API_TITLE, version=config.API_VERSION, lifespan=lifespan)
app.middleware("http")(upload_guard)
add_cors(app)      # el último en añadirse es el más externo
app.include_router(datos.router)
app.include_router(lectura.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(simulacion.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(auditoria.router)
app.include_router(seguridad.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(admin_claves.router)


@app.get(config.ROUTE_HEALTH)
def health() -> dict:
    return dict(config.HEALTH_OK)


@app.get(config.ROUTE_SITES, response_model=list[Site], dependencies=[Depends(datastore.refresh_if_stale)])
def sites() -> list[dict]:
    result = storage.read_sites()
    if result is None:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_NO_SITES)
    if not result.report.valido:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_SITES_INVALID)
    return [r.values for r in result.records]

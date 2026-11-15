from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.core import config
from app.core.cors import add_cors
from app.core.errors import register_handlers
from app.core.limits import UploadGuard
from app.core.logging_config import configurar_logging
from app.core.peticion import RequestContextMiddleware
from app.core.seguridad_http import SecurityHeadersMiddleware
from app.db.session import db_enabled
from app.routers import admin_claves, analisis, auditoria, auth, datos, horizons, lectura, seguridad, simulacion, sites, ventanas, proyeccion_admin
from app.schemas.seguridad import HealthOut
from app.services import datastore

@asynccontextmanager
async def lifespan(_: FastAPI):
    """En producción, con base de datos, la sal del HMAC de la IP es obligatoria."""
    if config.is_production() and db_enabled() and not config.get_ip_hash_salt():
        raise RuntimeError(config.MSG_AUDIT_SALT_REQUIRED)
    datastore.startup()          # producción exige DATA_BUCKET; descarga o siembra los datos
    yield


configurar_logging()
app = FastAPI(title=config.API_TITLE, version=config.API_VERSION, description=config.API_DESCRIPTION,
              openapi_tags=config.OPENAPI_TAGS, contact=config.API_CONTACT, license_info=config.API_LICENSE, lifespan=lifespan)
app.add_middleware(UploadGuard)
app.add_middleware(RequestContextMiddleware)      # por dentro de CORS: hasta el 500 lleva sus cabeceras
app.add_middleware(SecurityHeadersMiddleware)     # por fuera de RequestContext: también el 500 JSON lleva las cabeceras
add_cors(app)      # el último en añadirse es el más externo
register_handlers(app)
app.include_router(datos.router)
app.include_router(auth.router)
app.include_router(lectura.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(ventanas.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(proyeccion_admin.router)
app.include_router(sites.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(simulacion.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(analisis.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(horizons.router)
app.include_router(auditoria.router)
app.include_router(seguridad.router, dependencies=[Depends(datastore.refresh_if_stale)])
app.include_router(admin_claves.router)


@app.get(config.ROUTE_HEALTH, response_model=HealthOut, tags=[config.TAG_SYSTEM], summary="Service health")
def health() -> HealthOut:
    """Returns `{"status": "ok"}` while the service is up."""
    return HealthOut(**config.HEALTH_OK)

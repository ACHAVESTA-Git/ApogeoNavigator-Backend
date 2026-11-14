from fastapi import FastAPI, HTTPException

from app.core import config
from app.core.limits import upload_guard
from app.routers import datos
from app.schemas.sites import Site
from app.services import storage

app = FastAPI(title=config.API_TITLE, version=config.API_VERSION)
app.middleware("http")(upload_guard)
app.include_router(datos.router)


@app.get(config.ROUTE_HEALTH)
def health() -> dict:
    return dict(config.HEALTH_OK)


@app.get(config.ROUTE_SITES, response_model=list[Site])
def sites() -> list[dict]:
    result = storage.read_sites()
    if result is None:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_NO_SITES)
    if not result.report.valido:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_SITES_INVALID)
    return [r.values for r in result.records]

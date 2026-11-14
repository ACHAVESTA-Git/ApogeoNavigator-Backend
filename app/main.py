from fastapi import FastAPI, HTTPException

from app.core import config
from app.core.limits import upload_guard
from app.routers import datos
from app.schemas.sites import Site
from app.services import storage

app = FastAPI(title="Apogeo API", version="0.2.0")
app.middleware("http")(upload_guard)
app.include_router(datos.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/sites", response_model=list[Site])
def sites() -> list[dict]:
    result = storage.read_sites()
    if result is None:
        raise HTTPException(status_code=503, detail=config.MSG_NO_SITES)
    if not result.report.valido:
        raise HTTPException(status_code=503, detail=config.MSG_SITES_INVALID)
    return [r.values for r in result.records]

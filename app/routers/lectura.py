"""Endpoints de lectura para el frontend: efemérides, métricas recalculadas y puestas."""
from typing import Annotated, Callable

from fastapi import APIRouter, HTTPException, Query

from app.core import config as c
from app.schemas.lectura import EphemerisOut, MetricsOut, SunsetsOut
from app.services import lectura

router = APIRouter(prefix="/api", tags=[c.ROUTER_TAG_LECTURA])

RESPONSES = {
    404: {"description": "El sitio no existe o no tiene efemérides"},
    422: {"description": "Rango o parámetros no válidos"},
    503: {"description": "Faltan datos o no cumplen el estándar de CSV"},
}


def _run(fn: Callable, *args):
    try:
        return fn(*args)
    except lectura.LecturaError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.get("/efemerides/{site_id}", response_model=EphemerisOut, responses=RESPONSES)
def efemerides(site_id: str, desde: str | None = None, hasta: str | None = None):
    """Filas de efemérides del sitio entre `desde` y `hasta` (UTC `YYYY-MM-DDTHH:MMZ`, máx. 35 días)."""
    return _run(lectura.ephemeris_range, site_id, desde, hasta)


@router.get("/metricas", response_model=MetricsOut, responses=RESPONSES)
def metricas(site_id: Annotated[list[str], Query()] = [], desde: str | None = None, hasta: str | None = None):
    """Métricas recalculadas desde las efemérides, con semáforo provisional y comparación con
    `metricas.csv` si existe una fila para ese rango. Siempre `{"resultados": [...]}`.

    Intervalo semiabierto [desde, hasta), como `metricas.csv`; las efemérides usan [desde, hasta] cerrado.
    Un `desde` fuera de la rejilla de 1 h (p. ej. 00:30) se acepta, pero entonces `referencia` es null.
    Un `metricas.csv` ausente o inválido no bloquea: `referencia` es null."""
    return _run(lectura.metrics, site_id, desde, hasta)


@router.get("/puestas/{site_id}", response_model=SunsetsOut, responses=RESPONSES)
def puestas(site_id: str, desde: str | None = None):
    """Próxima puesta y salida del Sol y de la Tierra desde `desde`, y horas hasta el próximo cambio."""
    return _run(lectura.sunsets, site_id, desde)

"""Endpoints de lectura para el frontend: efemérides, métricas recalculadas y puestas."""
from typing import Annotated

from fastapi import APIRouter, Query

from app.core import config as c
from app.routers.comun import RESPONSES, run_service
from app.schemas.lectura import EphemerisOut, MetricsOut, SunsetsOut
from app.services import lectura

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_LECTURA])

@router.get(c.ROUTE_EFEMERIDES, response_model=EphemerisOut, responses=RESPONSES)
def efemerides(site_id: str, desde: str | None = None, hasta: str | None = None):
    """Filas de efemérides del sitio entre `desde` y `hasta` (UTC `YYYY-MM-DDTHH:MMZ`, máx. 35 días)."""
    return run_service(lectura.ephemeris_range, site_id, desde, hasta)


@router.get(c.ROUTE_METRICAS, response_model=MetricsOut, responses=RESPONSES)
def metricas(site_id: Annotated[list[str], Query()] = [], desde: str | None = None, hasta: str | None = None):
    """Métricas recalculadas desde las efemérides, con semáforo y comparación con
    `metricas.csv` si existe una fila para ese rango. Siempre `{"resultados": [...]}`.

    Intervalo semiabierto [desde, hasta), como `metricas.csv`; las efemérides usan [desde, hasta] cerrado.
    Un `desde` fuera de la rejilla de 1 h (p. ej. 00:30) se acepta, pero entonces `referencia` es null.
    Un `metricas.csv` ausente o inválido no bloquea: `referencia` es null."""
    return run_service(lectura.metrics, site_id, desde, hasta)


@router.get(c.ROUTE_PUESTAS, response_model=SunsetsOut, responses=RESPONSES)
def puestas(site_id: str, desde: str | None = None):
    """Próxima puesta y salida del Sol y de la Tierra desde `desde`, y horas hasta el próximo cambio."""
    return run_service(lectura.sunsets, site_id, desde)

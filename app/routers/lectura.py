"""Endpoints de lectura para el frontend: efemérides, métricas recalculadas y puestas."""
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.core import config as c
from app.routers.comun import RESPONSES
from app.schemas.lectura import EphemerisOut, MetricsOut, SunsetsOut
from app.services import lectura

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_LECTURA])


DESDE = Query(description="Inicio (UTC, `YYYY-MM-DDTHH:MMZ`). Por defecto, el inicio de los datos.", examples=["2026-12-01T00:00Z"])
HASTA = Query(description="Fin (UTC, `YYYY-MM-DDTHH:MMZ`). Por defecto, el fin del rango o el límite de 35 días.", examples=["2027-01-01T00:00Z"])
SITE_PATH = Path(description="Identificador del sitio (ver /api/sites).", examples=["shackleton"])


@router.get(c.ROUTE_EFEMERIDES, response_model=EphemerisOut, responses=RESPONSES, summary="Hourly Sun and Earth ephemerides of a site")
def efemerides(site_id: Annotated[str, SITE_PATH], desde: Annotated[str | None, DESDE] = None, hasta: Annotated[str | None, HASTA] = None):
    """Filas de efemérides del sitio entre `desde` y `hasta` (UTC `YYYY-MM-DDTHH:MMZ`, máx. 35 días)."""
    return lectura.ephemeris_range(site_id, desde, hasta)


@router.get(c.ROUTE_METRICAS, response_model=MetricsOut, responses=RESPONSES, summary="Sunlight and Earth-visibility metrics with a traffic light")
def metricas(site_id: Annotated[list[str], Query(description="Sitio a consultar; repítelo para comparar de 1 a 3 sitios.", examples=[["shackleton", "change4"]])] = [],
             desde: Annotated[str | None, DESDE] = None, hasta: Annotated[str | None, HASTA] = None):
    """Métricas recalculadas desde las efemérides, con semáforo y comparación con
    `metricas.csv` si existe una fila para ese rango. Siempre `{"resultados": [...]}`.

    Intervalo semiabierto [desde, hasta), como `metricas.csv`; las efemérides usan [desde, hasta] cerrado.
    Un `desde` fuera de la rejilla de 1 h (p. ej. 00:30) se acepta, pero entonces `referencia` es null.
    Un `metricas.csv` ausente o inválido no bloquea: `referencia` es null."""
    return lectura.metrics(site_id, desde, hasta)


@router.get(c.ROUTE_PUESTAS, response_model=SunsetsOut, responses=RESPONSES, summary="Next sunset and sunrise of the Sun and the Earth")
def puestas(site_id: Annotated[str, SITE_PATH], desde: Annotated[str | None, Query(description="Instante de referencia (UTC, `YYYY-MM-DDTHH:MMZ`). Por defecto, el inicio de los datos.", examples=["2026-12-01T00:00Z"])] = None):
    """Próxima puesta y salida del Sol y de la Tierra desde `desde`, y horas hasta el próximo cambio."""
    return lectura.sunsets(site_id, desde)

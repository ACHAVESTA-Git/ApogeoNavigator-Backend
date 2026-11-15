"""Endpoints de lectura para el frontend: efemérides, métricas recalculadas y puestas."""
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.core import config as c
from app.routers.comun import RESPONSES
from app.schemas.lectura import EphemerisOut, MetricsOut, SunsetsOut
from app.services import lectura

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_LECTURA])


DESDE = Query(description="Start (UTC, `YYYY-MM-DDTHH:MMZ`). Defaults to the start of the data.", examples=["2026-12-01T00:00Z"])
HASTA = Query(description="End (UTC, `YYYY-MM-DDTHH:MMZ`). Defaults to the end of the range or the 35-day limit.", examples=["2027-01-01T00:00Z"])
SITE_PATH = Path(description="Site identifier (see /api/sites).", examples=["shackleton"])


@router.get(c.ROUTE_EFEMERIDES, response_model=EphemerisOut, responses=RESPONSES, summary="Hourly Sun and Earth ephemerides of a site")
def efemerides(site_id: Annotated[str, SITE_PATH], desde: Annotated[str | None, DESDE] = None, hasta: Annotated[str | None, HASTA] = None):
    """Ephemeris rows of the site between `desde` and `hasta` (UTC `YYYY-MM-DDTHH:MMZ`, at most 35 days)."""
    return lectura.ephemeris_range(site_id, desde, hasta)


@router.get(c.ROUTE_METRICAS, response_model=MetricsOut, responses=RESPONSES, summary="Sunlight and Earth-visibility metrics with a traffic light")
def metricas(site_id: Annotated[list[str], Query(description="Site to query; repeat it to compare 1 to 3 sites.", examples=[["shackleton", "change4"]])] = [],
             desde: Annotated[str | None, DESDE] = None, hasta: Annotated[str | None, HASTA] = None):
    """Metrics recomputed from the ephemerides, with a traffic light and a comparison with `metricas.csv` when it has a row
    for that range. Always `{"resultados": [...]}`.

    Half-open interval [desde, hasta), like `metricas.csv`; the ephemerides use the closed interval [desde, hasta].
    A `desde` off the 1-hour grid (e.g. 00:30) is accepted, but then `referencia` is null.
    A missing or invalid `metricas.csv` does not block: `referencia` is null."""
    return lectura.metrics(site_id, desde, hasta)


@router.get(c.ROUTE_PUESTAS, response_model=SunsetsOut, responses=RESPONSES, summary="Next sunset and sunrise of the Sun and the Earth")
def puestas(site_id: Annotated[str, SITE_PATH], desde: Annotated[str | None, Query(description="Reference instant (UTC, `YYYY-MM-DDTHH:MMZ`). Defaults to the start of the data.", examples=["2026-12-01T00:00Z"])] = None):
    """Next set and rise of the Sun and the Earth from `desde`, and the hours until the next change."""
    return lectura.sunsets(site_id, desde)

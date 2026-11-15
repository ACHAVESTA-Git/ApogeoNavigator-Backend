"""Consulta en vivo a JPL Horizons para coordenadas propias: cualquier punto de la Luna, no solo los sitios del catálogo."""
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.core import config as c
from app.schemas.common import ErrorOut
from app.schemas.horizons import HorizonsOut
from app.services import horizons
from app.utils import auditoria_util

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_HORIZONS])

RESPONSES = {
    c.HTTP_UNPROCESSABLE: {"description": c.DESC_UNPROCESSABLE, "model": ErrorOut},
    c.HTTP_TOO_MANY_REQUESTS: {"description": c.DESC_TOO_MANY, "model": ErrorOut},
    c.HTTP_BAD_GATEWAY: {"description": c.DESC_BAD_GATEWAY, "model": ErrorOut},
    c.HTTP_GATEWAY_TIMEOUT: {"description": c.DESC_GATEWAY_TIMEOUT, "model": ErrorOut},
}


@router.get(c.ROUTE_HORIZONS, response_model=HorizonsOut, responses=RESPONSES,
            summary="Sun and Earth sky for custom lunar coordinates, queried live from JPL Horizons")
def consultar(request: Request,
              lat: Annotated[float, Query(ge=c.LAT_MIN, le=c.LAT_MAX, description="Latitude in degrees, -90 to 90 (south is negative)")],
              lon: Annotated[float, Query(ge=c.LON_MIN, le=c.LON_MAX, description="Longitude in degrees, -180 to 180 (east is positive)")],
              desde: Annotated[str, Query(description="Start, UTC `YYYY-MM-DDTHH:MMZ` (years 1900-2100)")],
              hasta: Annotated[str, Query(description="End, UTC `YYYY-MM-DDTHH:MMZ` (at most 35 days after `desde`)")],
              paso_horas: Annotated[int, Query(ge=c.HORIZONS_STEP_MIN_H, le=c.HORIZONS_STEP_MAX_H, description="Step in hours (1 to 24)")] = c.HORIZONS_STEP_DEFAULT_H):
    """Sun and Earth as seen from any point on the Moon, queried LIVE from NASA/JPL Horizons (observer `coord@301`,
    no refraction). Same format as `/api/efemerides/{site_id}`, plus `metricas` (with traffic light and warnings) and `puestas`.

    Coordinates are rounded to 4 decimals (about 3 m). Metrics use the interval [desde, hasta) and sunsets only look inside
    the requested range. Results are cached for 24 h and live queries are rate-limited (429 with `Retry-After`); if JPL
    fails the answer is 502, 504 if it takes too long, and 503 if the live-query queue is full.
    """
    return horizons.query(lat, lon, desde, hasta, paso_horas, client=auditoria_util.trusted_client_ip(request))

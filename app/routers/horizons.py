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


@router.get(c.ROUTE_HORIZONS, response_model=HorizonsOut, responses=RESPONSES)
def consultar(request: Request,
              lat: Annotated[float, Query(ge=c.LAT_MIN, le=c.LAT_MAX, description="Latitud en grados (sur negativo)")],
              lon: Annotated[float, Query(ge=c.LON_MIN, le=c.LON_MAX, description="Longitud en grados (Este positivo)")],
              desde: Annotated[str, Query(description="Inicio, UTC `YYYY-MM-DDTHH:MMZ`")],
              hasta: Annotated[str, Query(description="Fin, UTC `YYYY-MM-DDTHH:MMZ` (máx. 35 días después de `desde`)")],
              paso_horas: Annotated[int, Query(ge=c.HORIZONS_STEP_MIN_H, le=c.HORIZONS_STEP_MAX_H)] = c.HORIZONS_STEP_DEFAULT_H):
    """Sol y Tierra vistos desde un punto de la Luna, consultados EN VIVO a NASA/JPL Horizons (observador `coord@301`,
    sin refracción), con el mismo formato que `/api/efemerides/{site_id}` más `metricas` (con semáforo y avisos) y `puestas`.

    Las coordenadas se redondean a 4 decimales (≈ 3 m). Las métricas usan el intervalo [desde, hasta) y las puestas
    solo miran dentro del rango pedido. Los resultados se guardan en caché 24 h y hay un límite de consultas en vivo
    (429 con `Retry-After`); si JPL falla responde 502, y 504 si tarda demasiado.
    """
    return horizons.query(lat, lon, desde, hasta, paso_horas, client=auditoria_util.client_ip(request))

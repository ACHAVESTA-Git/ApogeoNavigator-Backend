"""Simulación del cielo lunar: datos reales dentro del rango y proyección estadística fuera de él."""
from fastapi import APIRouter

from app.analysis import simulacion
from app.core import config as c
from app.routers.lectura import RESPONSES, run_service
from app.schemas.simulacion import SimulacionOut

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_SIMULACION])


@router.get(c.ROUTE_SIMULACION, response_model=SimulacionOut, responses=RESPONSES)
def simular(site_id: str, desde: str | None = None, hasta: str | None = None,
            paso_horas: float = c.SIM_DEFAULT_STEP_HOURS):
    """Serie de azimut y elevación del Sol y de la Tierra para animar el cielo.

    Dentro de los datos reales cada punto trae `origen: "datos"`. Después del último dato, y hasta 366 días,
    `origen: "proyeccion"` con `incertidumbre_deg` (RMSE de la validación contra Horizons por mes de horizonte).
    La proyección es una estimación estadística: no sustituye a las efemérides físicas.
    """
    return run_service(simulacion.simulate, site_id, desde, hasta, paso_horas)

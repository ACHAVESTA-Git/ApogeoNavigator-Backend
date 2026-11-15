"""Simulación del cielo lunar: datos reales dentro del rango y proyección estadística fuera de él."""
from fastapi import APIRouter

from app.analysis import simulacion
from app.core import config as c
from app.routers.comun import RESPONSES
from app.schemas.simulacion import SimulacionOut

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_SIMULACION])


@router.get(c.ROUTE_SIMULACION, response_model=SimulacionOut, responses=RESPONSES,
            summary="Simulate the lunar sky: real data plus a statistical projection")
def simular(site_id: str, desde: str | None = None, hasta: str | None = None,
            paso_horas: float = c.SIM_DEFAULT_STEP_HOURS):
    """Serie de azimut y elevación del Sol y de la Tierra para animar el cielo.

    Dentro de los datos reales cada punto trae `origen: "datos"`. Después del último dato, y hasta 366 días,
    `origen: "proyeccion"` con `incertidumbre_deg`: el RMSE de validación contra Horizons por mes de horizonte. Es una cifra optimista y
    no un intervalo de confianza: el error real puede superarla.
    La proyección es una estimación estadística: no sustituye a las efemérides físicas.
    """
    return simulacion.simulate(site_id, desde, hasta, paso_horas)

"""Simulación del cielo lunar: datos reales dentro del rango y proyección estadística fuera de él."""
from fastapi import APIRouter

from app.analysis import simulacion
from app.core import config as c
from app.routers.comun import RESPONSES
from app.schemas.simulacion import SimulacionOut

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_SIMULACION])


@router.get(c.ROUTE_SIMULACION, response_model=SimulacionOut, responses=RESPONSES)
def simular(site_id: str, desde: str | None = None, hasta: str | None = None,
            paso_horas: float = c.SIM_DEFAULT_STEP_HOURS):
    """Serie de azimut y elevación del Sol y de la Tierra para animar el cielo.

    Dentro de los datos reales cada punto trae `origen: "datos"`. Después del último dato, y hasta 366 días,
    `origen: "proyeccion"` con `incertidumbre_deg`: el RMSE de validación contra Horizons por mes de horizonte. En los
    azimuts es el error de dirección escalado por 1/cos de la elevación del propio punto (con tope de 180°), porque cerca
    del cénit o del nadir el azimut gira de golpe y falla mucho más.

    `incertidumbre_deg` no es un intervalo de confianza, pero **2× esa cifra cubre el 98–100 % de los puntos** (medido
    contra Horizons en 14 640 puntos por variable; antes de escalar el azimut cubría el 97–99,6 %). `1×` cubre entre el
    54 % y el 87 %.

    `azimut_solar_fiable` es `false` en los puntos proyectados con el Sol a más de 70° de elevación o por debajo de −70°;
    el aviso `azimut_solar_cenit` sale cuando hay alguno entre los puntos pedidos y dice cuántos. La proyección es una
    estimación estadística: no sustituye a las efemérides físicas.
    """
    return simulacion.simulate(site_id, desde, hasta, paso_horas)

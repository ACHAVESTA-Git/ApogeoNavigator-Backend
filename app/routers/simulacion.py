"""Simulación del cielo lunar: datos reales dentro del rango y proyección estadística fuera de él."""
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.analysis import simulacion
from app.core import config as c
from app.routers.comun import RESPONSES
from app.schemas.simulacion import SimulacionOut

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_SIMULACION])


@router.get(c.ROUTE_SIMULACION, response_model=SimulacionOut, responses=RESPONSES,
            summary="Simulate the lunar sky: real data plus a statistical projection")
def simular(site_id: Annotated[str, Path(description="Site identifier (see /api/sites).", examples=["shackleton"])],
            desde: Annotated[str | None, Query(description="Start (UTC, `YYYY-MM-DDTHH:MMZ`). Defaults to the start of the data.",
                                               examples=["2027-05-31T23:00Z"])] = None,
            hasta: Annotated[str | None, Query(description="End (UTC, `YYYY-MM-DDTHH:MMZ`). Defaults to 30 days after `desde`; "
                                                           "up to 366 days after the last data point can be projected.",
                                               examples=["2027-06-01T03:00Z"])] = None,
            paso_horas: Annotated[float, Query(description="Spacing between points, in hours (between 0.25 and 24; the series is "
                                                           "capped at 9000 points).", examples=[1.0])] = c.SIM_DEFAULT_STEP_HOURS):
    """Series of Sun and Earth azimuth and elevation, to animate the sky.

    Inside the real data every point has `origen: "datos"`. After the last data point, and up to 366 days, points have
    `origen: "proyeccion"` with `incertidumbre_deg`: the validation RMSE against Horizons by horizon month. For the
    azimuths it is the direction error of the body divided by cos of the point's own elevation (capped at 180°),
    because near the zenith or nadir the azimuth turns abruptly and fails much more.

    `incertidumbre_deg` is not a confidence interval, but **2x that figure covers 98-100% of the points** (measured
    against Horizons over 14,640 points per variable; 97-99.6% before the azimuth was scaled). 1x covers 54-87%.

    `azimut_solar_fiable` is `false` for projected points with the Sun above 70° of elevation or below -70°; the
    `azimut_solar_cenit` warning appears when any requested point is in that case and says how many. The warning
    `incertidumbre_de_otros_datos` appears when the site's CSV is not the one the model was validated with (for example,
    a user uploaded new ephemerides): the uncertainty is then a reference from other data. The projection is a
    statistical estimate: it does not replace the physical ephemerides.
    """
    return simulacion.simulate(site_id, desde, hasta, paso_horas)

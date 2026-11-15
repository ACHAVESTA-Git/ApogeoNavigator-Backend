"""Ventanas de alunizaje: mejor hora de llegada para una estancia de 72, 156 o 336 horas."""
from typing import Annotated

from fastapi import APIRouter, Query

from app.core import config as c
from app.routers.comun import RESPONSES
from app.routers.lectura import DESDE, HASTA
from app.schemas.ventanas import LandingWindowsOut
from app.services import ventanas

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_LECTURA])


@router.get(c.ROUTE_VENTANAS, response_model=LandingWindowsOut, responses=RESPONSES,
            summary="Best landing windows for a stay of 72, 156 or 336 hours")
def ventanas_alunizaje(
        site_id: Annotated[list[str], Query(default_factory=list, description=f"Site to query; repeat it for 1 to {c.MAX_VENTANAS_SITIOS} sites.", examples=[["shackleton", "apollo11"]])],
        estancia_h: Annotated[str | None, Query(description="Length of the stay in hours: 72, 156 or 336.", examples=["156"])] = None,
        desde: Annotated[str | None, DESDE] = None, hasta: Annotated[str | None, HASTA] = None):
    """For every possible arrival hour `t` of each site, how much Sun and Earth the stay `[t, t + estancia_h)` gets:
    hours and percentages of Sun, Earth and both at once, whether the Sun and the Earth are up on arrival and the longest
    stretch without the Earth. Returns the best arrival hour of each day (`serie_diaria`, for a heat calendar), the 3 best
    non-overlapping windows per site (`mejores`) and, with several sites, the best of all (`mejor_global`).

    The range defaults to all the data and has no 35-day limit. Arrival hours whose stay would run past `hasta` (or the end
    of the data) are dropped and counted in `ventanas_descartadas`, with `trunca` set. Ideal horizon: real terrain can only
    make the figures worse."""
    return ventanas.landing_windows(site_id, estancia_h, desde, hasta)

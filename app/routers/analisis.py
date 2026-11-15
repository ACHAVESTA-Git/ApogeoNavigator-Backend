"""Transparencia del análisis: cómo se analizan los datos de un sitio, paso a paso, con números reales."""
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.analysis import transparencia
from app.core import config as c
from app.routers.comun import RESPONSES
from app.schemas.analisis import AnalisisOut

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_ANALISIS])


@router.get(c.ROUTE_ANALISIS, response_model=AnalisisOut, responses=RESPONSES,
            summary="Explain how a site's data is analysed, step by step")
def analisis(site_id: Annotated[str, Path(description="Site identifier (see /api/sites).", examples=["shackleton"])],
             desde: Annotated[str | None, Query(description="Start of the plotted series (UTC, `YYYY-MM-DDTHH:MMZ`). Defaults to the "
                                                            "start of the data.", examples=["2026-12-01T00:00Z"])] = None,
             hasta: Annotated[str | None, Query(description="End of the plotted series (UTC, `YYYY-MM-DDTHH:MMZ`). Defaults to 90 days "
                                                            "after the last data point; up to 366 days can be projected.",
                                                examples=["2027-08-30T00:00Z"])] = None,
             variable: Annotated[str | None, Query(description="Variable of the plotted series: `sol_el_deg` (default), `sol_az_deg`, "
                                                               "`tierra_az_deg` or `tierra_el_deg`.", examples=["sol_el_deg"])] = None):
    """The analysis pipeline (load, validation, Polars features, FFT, harmonic fit, validation against Horizons, projection),
    the input with its origin and quality, the spectrum, the model, the validation with its limits and a small
    observed / fitted / projected series.

    It reflects the site's current data, including anything a user uploaded. The validation against Horizons is
    `vigente` only if it was done with those same data (the file hash is compared). R² and residuals are in-sample.
    It does not replace the physical ephemerides.
    """
    return transparencia.analyse(site_id, desde, hasta, variable)

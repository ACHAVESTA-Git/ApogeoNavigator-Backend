"""Transparencia del análisis: cómo se analizan los datos de un sitio, paso a paso, con números reales."""
from fastapi import APIRouter

from app.analysis import transparencia
from app.core import config as c
from app.routers.comun import RESPONSES
from app.schemas.analisis import AnalisisOut

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_ANALISIS])


@router.get(c.ROUTE_ANALISIS, response_model=AnalisisOut, responses=RESPONSES)
def analisis(site_id: str, desde: str | None = None, hasta: str | None = None, variable: str | None = None):
    """Pipeline del análisis (carga, validación, características, FFT, ajuste, validación y proyección), la entrada
    con su origen y su calidad, el espectro, el modelo, la validación contra Horizons con sus límites y una serie
    pequeña «observado, ajustado y proyectado» de `variable` (por defecto `sol_el_deg`) entre `desde` y `hasta`.

    Refleja los datos actuales del sitio, incluidos los que haya subido un usuario. La validación contra Horizons solo
    es `vigente` si se hizo con esos mismos datos. No sustituye a las efemérides físicas.
    """
    return transparencia.analyse(site_id, desde, hasta, variable)

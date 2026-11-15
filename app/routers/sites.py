"""Catálogo de sitios de alunizaje."""
from fastapi import APIRouter

from app.core import config as c
from app.schemas.sites import Site
from app.services import lectura

router = APIRouter(tags=[c.TAG_SITES])


@router.get(c.ROUTE_SITES, response_model=list[Site], summary="List the landing sites")
def sites() -> list[dict]:
    """The ten sites of the catalogue: coordinates (latitude south negative, longitude east positive), mission, agency and the
    URL of the NASA source of each position."""
    return lectura.sites_list()

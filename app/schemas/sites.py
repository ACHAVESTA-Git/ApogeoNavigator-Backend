from typing import Literal

from pydantic import BaseModel

from app.core import config


class Site(BaseModel):
    site_id: str
    nombre: str
    categoria: Literal[*config.SITE_CATEGORIAS]
    mision: str
    agencia: str
    lat_deg: float
    lon_este_deg: float
    alt_km: float = config.ALT_KM_DEFAULT
    fuente_url: str
    notas: str | None = None

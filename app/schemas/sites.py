from pydantic import BaseModel


class Site(BaseModel):
    site_id: str
    nombre: str
    categoria: str
    mision: str
    agencia: str
    lat_deg: float
    lon_este_deg: float
    alt_km: float = 0.0
    fuente_url: str
    notas: str | None = None

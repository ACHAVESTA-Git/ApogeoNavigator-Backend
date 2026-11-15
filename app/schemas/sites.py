from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import config


class Site(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "site_id": "apollo11", "nombre": "Apolo 11 - Mar de la Tranquilidad", "categoria": "apolo", "mision": "Apollo 11",
        "agencia": "NASA", "lat_deg": 0.67416, "lon_este_deg": 23.47314, "alt_km": 0.0,
        "fuente_url": "https://nssdc.gsfc.nasa.gov/planetary/lunar/lunar_sites.html", "notas": None}]})

    site_id: str = Field(description="Stable site identifier (lower case, no spaces).")
    nombre: str = Field(description="Human-readable site name.")
    categoria: Literal[*config.SITE_CATEGORIAS] = Field(description="Site family: apolo, artemis, clps, internacional, historico or personalizado.")
    mision: str = Field(description="Associated mission.")
    agencia: str = Field(description="Agency or operator.")
    lat_deg: float = Field(description="Latitude in degrees, -90 to 90 (south negative).")
    lon_este_deg: float = Field(description="Longitude in degrees, -180 to 180 (east positive, west negative).")
    alt_km: float = Field(config.ALT_KM_DEFAULT, description="Altitude in km relative to the 1737.4 km sphere; 0 if not given.")
    fuente_url: str = Field(description="URL of the official source of the position (NSSDC, LROC or NASA).")
    notas: str | None = Field(None, description="Notes about the position; null if none.")

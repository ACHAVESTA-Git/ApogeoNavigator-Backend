from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import config


class Site(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "site_id": "apollo11", "nombre": "Apolo 11 - Mar de la Tranquilidad", "categoria": "apolo", "mision": "Apollo 11",
        "agencia": "NASA", "lat_deg": 0.67416, "lon_este_deg": 23.47314, "alt_km": 0.0,
        "fuente_url": "https://nssdc.gsfc.nasa.gov/planetary/lunar/lunar_sites.html", "notas": None}]})

    site_id: str = Field(description="Identificador estable del sitio (minúsculas, sin espacios).")
    nombre: str = Field(description="Nombre legible del sitio.")
    categoria: Literal[*config.SITE_CATEGORIAS] = Field(description="Familia del sitio: apolo, artemis, clps, internacional, historico o personalizado.")
    mision: str = Field(description="Misión asociada.")
    agencia: str = Field(description="Agencia u operador.")
    lat_deg: float = Field(description="Latitud en grados, de -90 a 90 (sur negativo).")
    lon_este_deg: float = Field(description="Longitud en grados, de -180 a 180 (este positivo, oeste negativo).")
    alt_km: float = Field(config.ALT_KM_DEFAULT, description="Altitud en km respecto a la esfera de 1737,4 km; 0 si no se indica.")
    fuente_url: str = Field(description="URL de la fuente oficial de la posición (NSSDC, LROC o NASA).")
    notas: str | None = Field(None, description="Notas sobre la posición; null si no hay.")

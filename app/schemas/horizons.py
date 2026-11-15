"""Modelos de respuesta de la consulta en vivo a JPL Horizons (coordenadas propias)."""
from pydantic import BaseModel

from app.schemas.lectura import EphemerisRow, MetricsItem, SunsetsOut


class HorizonsOut(BaseModel):
    """Same format as `/api/efemerides/{site_id}` (`site_id`, `inicio_utc`, `fin_utc`, `paso_horas`, `filas`) plus the
    queried position, the source, the metrics of the range and the sunsets from `inicio_utc`."""
    site_id: str
    inicio_utc: str
    fin_utc: str
    paso_horas: float
    filas: list[EphemerisRow]
    fuente: str
    lat_deg: float
    lon_este_deg: float
    en_cache: bool
    consultado_utc: str
    metricas: MetricsItem
    puestas: SunsetsOut

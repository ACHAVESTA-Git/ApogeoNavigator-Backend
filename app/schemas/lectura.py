"""Modelos de respuesta de los endpoints de lectura."""
from pydantic import BaseModel


class EphemerisRow(BaseModel):
    utc: str
    sol_az_deg: float
    sol_el_deg: float
    tierra_az_deg: float
    tierra_el_deg: float


class EphemerisOut(BaseModel):
    site_id: str
    inicio_utc: str
    fin_utc: str
    paso_horas: float
    filas: list[EphemerisRow]


class Reference(BaseModel):
    horas_sol: float
    pct_sol: float
    sol_el_max_deg: float
    sol_el_media_deg: float | None
    horas_tierra_visible: float
    pct_tierra: float
    coincide: bool


class Aviso(BaseModel):
    codigo: str
    mensaje: str


class MetricsItem(BaseModel):
    site_id: str
    inicio_utc: str
    fin_utc: str
    horas_sol: float
    pct_sol: float
    sol_el_max_deg: float
    sol_el_media_deg: float | None
    horas_tierra_visible: float
    pct_tierra: float
    semaforo: dict[str, str]
    avisos: list[Aviso] = []
    origen: str
    referencia: Reference | None


class MetricsOut(BaseModel):
    resultados: list[MetricsItem]


class BodyEvents(BaseModel):
    visible: bool
    proxima_puesta_utc: str | None
    proxima_salida_utc: str | None
    horas_restantes: float | None


class SunsetsOut(BaseModel):
    site_id: str
    desde_utc: str
    sol: BodyEvents
    tierra: BodyEvents

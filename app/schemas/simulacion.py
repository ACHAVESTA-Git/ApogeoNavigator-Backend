"""Modelos de respuesta del endpoint de simulación del cielo."""
from typing import Literal

from pydantic import BaseModel

from app.core import config as c
from app.schemas.lectura import Aviso


class Incertidumbre(BaseModel):
    sol_az_deg: float
    sol_el_deg: float
    tierra_az_deg: float
    tierra_el_deg: float


class Punto(BaseModel):
    utc: str
    origen: Literal[c.ORIGEN_DATOS, c.ORIGEN_PROYECCION]
    sol_az_deg: float
    sol_el_deg: float
    tierra_az_deg: float
    tierra_el_deg: float
    incertidumbre_deg: Incertidumbre | None  # solo en la proyección: RMSE de validación (optimista; no es un intervalo)


class SimulacionOut(BaseModel):
    site_id: str
    desde_utc: str
    hasta_utc: str
    paso_horas: float
    fin_datos_utc: str
    avisos: list[Aviso]
    puntos: list[Punto]

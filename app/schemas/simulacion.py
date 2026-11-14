"""Modelos de respuesta del endpoint de simulación del cielo."""
from pydantic import BaseModel


class Incertidumbre(BaseModel):
    sol_az_deg: float
    sol_el_deg: float
    tierra_az_deg: float
    tierra_el_deg: float


class Punto(BaseModel):
    utc: str
    origen: str                              # "datos" o "proyeccion"
    sol_az_deg: float
    sol_el_deg: float
    tierra_az_deg: float
    tierra_el_deg: float
    incertidumbre_deg: Incertidumbre | None  # solo en la proyección: RMSE de la validación contra Horizons


class Aviso(BaseModel):
    codigo: str
    mensaje: str


class SimulacionOut(BaseModel):
    site_id: str
    desde_utc: str
    hasta_utc: str
    paso_horas: float
    fin_datos_utc: str
    avisos: list[Aviso]
    puntos: list[Punto]

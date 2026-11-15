"""Modelos de respuesta de GET /api/proyeccion (solo administradores)."""
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcStr
from app.schemas.lectura import AstronautIndicators, UTC_NOTE


class RmseDeg(BaseModel):
    sol_el_deg: float | None = Field(description="RMSE of the Sun elevation in degrees; null if the model file does not give it.")
    tierra_el_deg: float | None = Field(description="RMSE of the Earth elevation in degrees; null if unknown.")
    sol_az_deg: float | None = Field(description="RMSE of the Sun azimuth in degrees; null if unknown.")
    tierra_az_deg: float | None = Field(description="RMSE of the Earth azimuth in degrees; null if unknown.")


class HorizonError(BaseModel):
    meses: float | None = Field(description="Months after the last real data point.")
    rmse_sol_el_deg: float | None = Field(description="Sun elevation RMSE at that horizon, in degrees.")
    rmse_tierra_el_deg: float | None = Field(description="Earth elevation RMSE at that horizon, in degrees.")


class ControlPoint(BaseModel):
    site_id: str | None = Field(description="Site of the control query to JPL Horizons.")
    utc: str | None = Field(description=f"Instant of the control query ({UTC_NOTE}).")
    variable: str | None = Field(description="Variable compared (for example `sol_el_deg`).")
    error_deg: float | None = Field(description="Model minus Horizons, in degrees.")


class TrafficLightHits(BaseModel):
    aciertos: float | None = Field(description="Control months where the projected traffic light matched the real one.")
    total: float | None = Field(description="Control months compared.")


class ModelError(BaseModel):
    rmse_deg: RmseDeg = Field(description="Model error measured against real data, by variable.")
    por_horizonte: list[HorizonError] | None = Field(description="Error by time horizon; null if the model file does not give it.")
    control_horizons: list[ControlPoint] | None = Field(description="Control queries to JPL Horizons; null if unknown.")
    aciertos_semaforo_control: TrafficLightHits | None = Field(description="Traffic-light agreement in the control period; null if unknown.")


class ProjectionModel(BaseModel):
    version: str | None = Field(description="Model version.")
    metodo: str | None = Field(description="Method used to project.")
    entrenado_desde: str | None = Field(description=f"Start of the training data ({UTC_NOTE}).")
    entrenado_hasta: str | None = Field(description=f"End of the training data ({UTC_NOTE}).")
    proyectado_hasta: str | None = Field(description=f"End of the projection ({UTC_NOTE}).")
    error: ModelError = Field(description="Measured error of the model. Never invented: unknown values are null.")


class Limitation(BaseModel):
    codigo: str = Field(description="Stable limitation code.")
    mensaje: str = Field(description="Explanatory text to show to the user.")


class ProjectedPoint(BaseModel):
    utc: UtcStr = Field(description=f"Instant ({UTC_NOTE}).")
    sol_az_deg: float = Field(description="Projected Sun azimuth in degrees.")
    sol_el_deg: float = Field(description="Projected Sun elevation in degrees.")
    tierra_az_deg: float = Field(description="Projected Earth azimuth in degrees.")
    tierra_el_deg: float = Field(description="Projected Earth elevation in degrees.")


class ProjectedLight(BaseModel):
    energia: str = Field(description="Energy level by % of Sun (verde, amarillo or rojo).")
    comunicacion: str = Field(description="Communication level by % of Earth (verde, amarillo or rojo).")


class ProjectedMonth(BaseModel):
    mes: str = Field(description="Calendar month, `YYYY-MM` (the first and last may be partial).", examples=["2027-09"])
    pct_sol: float = Field(description="Projected percentage of the month with the Sun visible.")
    pct_tierra: float = Field(description="Projected percentage of the month with the Earth visible.")
    semaforo: ProjectedLight = Field(description="Traffic light computed with the same thresholds as the real metrics.")
    indicadores_astronauta: AstronautIndicators = Field(description="Astronaut indicators of the month, from the same utility as `/api/metricas`.")


class ProjectedSite(BaseModel):
    site_id: str = Field(description="Site identifier.")
    sha3_256: str | None = Field(description="SHA3-256 of the projection file this site was read from, so it can be checked; null if there is no file.")
    serie: list[ProjectedPoint] = Field(description="Projected series; empty if the site has no projection loaded.")
    meses: list[ProjectedMonth] = Field(description="Monthly metrics, hourly basis; empty if the site has no projection loaded.")


class ProjectionOut(BaseModel):
    origen: Literal["proyeccion"] = Field(description="Always `proyeccion`: modelled values, not JPL ephemerides.")
    modelo: ProjectionModel = Field(description="The model and its measured error.")
    limitaciones: list[Limitation] = Field(description="Explicit limitations that must accompany the figures.")
    resultados: list[ProjectedSite] = Field(description="One result per requested site, in request order.")

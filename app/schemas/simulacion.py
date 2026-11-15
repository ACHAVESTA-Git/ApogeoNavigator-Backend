"""Modelos de respuesta del endpoint de simulación del cielo (con descripciones y un ejemplo real para /docs)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import config as c
from app.schemas.lectura import Aviso


class Incertidumbre(BaseModel):
    sol_az_deg: float = Field(description="Validation RMSE of the projected Sun azimuth, in degrees.")
    sol_el_deg: float = Field(description="Validation RMSE of the projected Sun elevation, in degrees.")
    tierra_az_deg: float = Field(description="Validation RMSE of the projected Earth azimuth, in degrees.")
    tierra_el_deg: float = Field(description="Validation RMSE of the projected Earth elevation, in degrees.")


class Punto(BaseModel):
    utc: str = Field(description="Instant of the point (UTC, YYYY-MM-DDTHH:MMZ format).")
    origen: Literal[c.ORIGEN_DATOS, c.ORIGEN_PROYECCION] = Field(
        description="`datos`: real Horizons data. `proyeccion`: statistical estimate beyond the last data point.")
    sol_az_deg: float = Field(description="Sun azimuth in degrees, clockwise from north.")
    sol_el_deg: float = Field(description="Sun elevation above the ideal horizon, in degrees.")
    tierra_az_deg: float = Field(description="Earth azimuth in degrees, clockwise from north.")
    tierra_el_deg: float = Field(description="Earth elevation above the ideal horizon, in degrees.")
    azimut_solar_fiable: bool = Field(
        description="False for a projected point whose Sun is above 70° of elevation or below -70° (near zenith or nadir): "
                    "its azimuth is unreliable and `incertidumbre_deg.sol_az_deg` is large.")
    incertidumbre_deg: Incertidumbre | None = Field(
        description="Only in projected points: validation RMSE against Horizons by horizon month (azimuths scaled by 1/cos of the "
                    "point's elevation). Not a confidence interval, but 2x this figure covers 98-100% of points.")


class SimulacionOut(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "site_id": "shackleton", "desde_utc": "2027-05-31T23:00Z", "hasta_utc": "2027-06-01T03:00Z", "paso_horas": 2.0,
        "fin_datos_utc": "2027-06-01T00:00Z",
        "avisos": [{"codigo": "proyeccion_estadistica", "mensaje": "Los puntos con origen «proyeccion» son una estimación estadística, "
                                                                  "no efemérides físicas: sirven para explorar tendencias y animar el cielo. "
                                                                  "La incertidumbre sale de la validación contra Horizons."}],
        "puntos": [
            {"utc": "2027-05-31T23:00Z", "origen": "datos", "sol_az_deg": 94.7103, "sol_el_deg": -1.4204, "tierra_az_deg": 223.8475,
             "tierra_el_deg": 5.5166, "azimut_solar_fiable": True, "incertidumbre_deg": None},
            {"utc": "2027-06-01T01:00Z", "origen": "proyeccion", "sol_az_deg": 93.6879, "sol_el_deg": -1.4176, "tierra_az_deg": 223.6033,
             "tierra_el_deg": 5.4245, "azimut_solar_fiable": True, "incertidumbre_deg": {"sol_az_deg": 0.0548, "sol_el_deg": 0.0463, "tierra_az_deg": 0.227,
                                                            "tierra_el_deg": 0.1243}},
        ]}]})

    site_id: str = Field(description="Site identifier (see /api/sites).")
    desde_utc: str = Field(description="First instant of the series (UTC, YYYY-MM-DDTHH:MMZ format).")
    hasta_utc: str = Field(description="Last instant of the series (UTC, YYYY-MM-DDTHH:MMZ format).")
    paso_horas: float = Field(description="Spacing between points, in hours.")
    fin_datos_utc: str = Field(description="Last instant covered by real data; later points are projections.")
    avisos: list[Aviso] = Field(description="Warnings about the series (e.g. that the projection is statistical).")
    puntos: list[Punto] = Field(description="Sun and Earth azimuth and elevation at each instant.")

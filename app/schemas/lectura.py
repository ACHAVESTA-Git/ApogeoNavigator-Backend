"""Modelos de respuesta de los endpoints de lectura (con descripciones y ejemplos para /docs)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import config
from app.schemas.common import UtcStr

Nivel = Literal[*config.SEMAFORO_NIVELES]

UTC_NOTE = "UTC, YYYY-MM-DDTHH:MMZ format"


def _ejemplo(valor: dict) -> ConfigDict:
    return ConfigDict(json_schema_extra={"examples": [valor]})


class EphemerisRow(BaseModel):
    utc: UtcStr = Field(description=f"Instant of the sample ({UTC_NOTE}).")
    sol_az_deg: float = Field(description="Sun azimuth in degrees, measured clockwise from north (0 to 360).")
    sol_el_deg: float = Field(description="Sun elevation above the ideal horizon, in degrees (positive = visible).")
    tierra_az_deg: float = Field(description="Earth azimuth in degrees (0 to 360).")
    tierra_el_deg: float = Field(description="Earth elevation above the ideal horizon, in degrees.")


class EphemerisOut(BaseModel):
    model_config = _ejemplo({
        "site_id": "shackleton", "inicio_utc": "2026-12-01T00:00Z", "fin_utc": "2026-12-01T02:00Z", "paso_horas": 1.0,
        "filas": [
            {"utc": "2026-12-01T00:00Z", "sol_az_deg": 150.867622, "sol_el_deg": 1.173151, "tierra_az_deg": 237.476719, "tierra_el_deg": -1.792982},
            {"utc": "2026-12-01T01:00Z", "sol_az_deg": 150.36106, "sol_el_deg": 1.17439, "tierra_az_deg": 237.493202, "tierra_el_deg": -1.856755},
            {"utc": "2026-12-01T02:00Z", "sol_az_deg": 149.854487, "sol_el_deg": 1.175655, "tierra_az_deg": 237.50908, "tierra_el_deg": -1.920325},
        ]})
    site_id: str = Field(description="Site identifier (see /api/sites).")
    inicio_utc: UtcStr = Field(description=f"First instant returned ({UTC_NOTE}).")
    fin_utc: UtcStr = Field(description=f"Last instant returned, inclusive ({UTC_NOTE}).")
    paso_horas: float = Field(description="Spacing between samples, in hours (1.0 in the current data).")
    filas: list[EphemerisRow] = Field(description="Hourly samples between inicio_utc and fin_utc, both inclusive.")


class Reference(BaseModel):
    horas_sol: float = Field(description="Hours with the Sun above the horizon according to metricas.csv (independent reference).")
    pct_sol: float = Field(description="Percentage of the interval with the Sun visible, according to the reference.")
    sol_el_max_deg: float = Field(description="Maximum Sun elevation in degrees, according to the reference.")
    sol_el_media_deg: float | None = Field(description="Mean Sun elevation while visible; null if it is never visible.")
    horas_tierra_visible: float = Field(description="Hours with the Earth above the horizon, according to the reference.")
    pct_tierra: float = Field(description="Percentage of the interval with the Earth visible, according to the reference.")
    coincide: bool = Field(description="true if the reference matches what is recomputed from the ephemerides.")


class Aviso(BaseModel):
    codigo: str = Field(description="Stable warning code (today only `sensible_al_relieve`).")
    mensaje: str = Field(description="Explanatory text to show to the user.")


class Semaforo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    energia: Nivel = Field(description=(
        f"By % of time with the Sun: verde from {config.SEMAFORO_ENERGIA_VERDE_MIN:g} %, amarillo from "
        f"{config.SEMAFORO_ENERGIA_AMARILLO_MIN:g} % up to {config.SEMAFORO_ENERGIA_VERDE_MIN:g} %, rojo below."))
    comunicacion: Nivel = Field(description=(
        f"By % of time with the Earth: verde from {config.SEMAFORO_COMUNICACION_VERDE_MIN:g} %, amarillo from "
        f"{config.SEMAFORO_COMUNICACION_AMARILLO_MIN:g} % up to {config.SEMAFORO_COMUNICACION_VERDE_MIN:g} %, rojo below."))
    global_: Nivel = Field(alias="global", description="The worse of the two levels above.")


class Streak(BaseModel):
    horas: float = Field(description="Length of the longest continuous stretch, in hours (0 if there is none).")
    inicio_utc: UtcStr | None = Field(description=f"Start of the stretch ({UTC_NOTE}); null if there is none.")
    fin_utc: UtcStr | None = Field(description=f"End of the stretch, i.e. the first sample where the state changes, or the end of the range ({UTC_NOTE}); null if there is none.")
    trunca_inicio: bool = Field(description="true if the stretch starts at the first sample of the range, so it may have begun earlier.")
    trunca_fin: bool = Field(description="true if the stretch reaches the end of the range, so it may last longer.")


class AstronautIndicators(BaseModel):
    noche_mas_larga: Streak = Field(description="Longest continuous night: Sun at or below the horizon.")
    apagon_tierra_mas_largo: Streak = Field(description="Longest continuous Earth blackout: Earth at or below the horizon (no direct link).")
    ventana_sol_y_tierra: Streak = Field(description="Longest continuous window with the Sun and the Earth above the horizon at once.")
    proxima_puesta_sol_utc: UtcStr | None = Field(description=f"First Sun set after the start of the range ({UTC_NOTE}); null if there is none in the range.")
    proxima_puesta_tierra_utc: UtcStr | None = Field(description=f"First Earth set after the start of the range ({UTC_NOTE}); null if there is none in the range.")
    sol_el_media_horas_sol_deg: float | None = Field(description="Mean Sun elevation during the hours of sunlight; null if the Sun never rises. Same value as `sol_el_media_deg`, grouped here with the other indicators.")


class MetricsItem(BaseModel):
    model_config = _ejemplo({
        "site_id": "shackleton", "inicio_utc": "2026-12-01T00:00Z", "fin_utc": "2027-01-01T00:00Z", "horas_sol": 744.0,
        "pct_sol": 100.0, "sol_el_max_deg": 1.7385, "sol_el_media_deg": 1.2929, "horas_tierra_visible": 291.0, "pct_tierra": 39.11,
        "semaforo": {"energia": "verde", "comunicacion": "rojo", "global": "rojo"},
        "avisos": [{"codigo": "sensible_al_relieve", "mensaje": "Sensible al relieve: el Sol no supera 3° sobre el horizonte ideal, "
                                                                "así que una colina puede taparlo y las horas de sol reales ser menores"}],
        "origen": "calculado",
        "referencia": {"horas_sol": 744.0, "pct_sol": 100.0, "sol_el_max_deg": 1.7385, "sol_el_media_deg": 1.2929,
                       "horas_tierra_visible": 291.0, "pct_tierra": 39.11, "coincide": True},
        "indicadores_astronauta": {
            "noche_mas_larga": {"horas": 0.0, "inicio_utc": None, "fin_utc": None, "trunca_inicio": False, "trunca_fin": False},
            "apagon_tierra_mas_largo": {"horas": 291.0, "inicio_utc": "2026-12-14T23:00Z", "fin_utc": "2026-12-27T02:00Z",
                                        "trunca_inicio": False, "trunca_fin": False},
            "ventana_sol_y_tierra": {"horas": 120.0, "inicio_utc": "2026-12-01T00:00Z", "fin_utc": "2026-12-06T00:00Z",
                                     "trunca_inicio": True, "trunca_fin": False},
            "proxima_puesta_sol_utc": None, "proxima_puesta_tierra_utc": "2026-12-06T00:00Z", "sol_el_media_horas_sol_deg": 1.2929}})
    site_id: str = Field(description="Site identifier.")
    inicio_utc: UtcStr = Field(description=f"Start of the interval, inclusive ({UTC_NOTE}).")
    fin_utc: UtcStr = Field(description=f"End of the interval, exclusive ({UTC_NOTE}).")
    horas_sol: float = Field(description="Hours of the interval with the Sun above the ideal horizon.")
    pct_sol: float = Field(description="Percentage of the interval with the Sun visible (0 to 100).")
    sol_el_max_deg: float = Field(description="Maximum Sun elevation in the interval, in degrees.")
    sol_el_media_deg: float | None = Field(description="Mean Sun elevation while visible; null if it is never visible.")
    horas_tierra_visible: float = Field(description="Hours of the interval with the Earth above the horizon (direct link possible).")
    pct_tierra: float = Field(description="Percentage of the interval with the Earth visible (0 to 100).")
    semaforo: Semaforo = Field(description="Level (verde, amarillo or rojo) of `energia` (by % of Sun), `comunicacion` (by % of Earth) and `global` (the worst of the two).")
    avisos: list[Aviso] = Field(default_factory=list, description="Warnings that accompany the traffic light; empty if there are none.")
    origen: Literal[*config.ORIGENES] = Field(description="`calculado`: recomputed from the ephemerides; `horizons_en_vivo`: computed from a live JPL Horizons query.")
    referencia: Reference | None = Field(description="Row of metricas.csv with the same site and range; null if there is none or the range does not match.")
    indicadores_astronauta: AstronautIndicators = Field(description="Indicators for astronauts, computed over the same range: longest night, longest Earth blackout, longest Sun-and-Earth window, next Sun and Earth sets and mean Sun elevation in daylight.")


class MetricsOut(BaseModel):
    resultados: list[MetricsItem] = Field(description="One metric per requested site, in request order.")


class BodyEvents(BaseModel):
    visible: bool = Field(description="true if the body is above the horizon at `desde_utc`.")
    proxima_puesta_utc: UtcStr | None = Field(description=f"Next set of the body ({UTC_NOTE}); null if there is none in the data.")
    proxima_salida_utc: UtcStr | None = Field(description=f"Next rise of the body ({UTC_NOTE}); null if there is none in the data.")
    horas_restantes: float | None = Field(description="Hours until the next change (set if visible, rise if not); null if none.")


class SunsetsOut(BaseModel):
    model_config = _ejemplo({
        "site_id": "shackleton", "desde_utc": "2026-12-01T00:00Z",
        "sol": {"visible": True, "proxima_puesta_utc": "2027-01-27T11:00Z", "proxima_salida_utc": "2027-01-29T17:00Z", "horas_restantes": 1379.0},
        "tierra": {"visible": False, "proxima_puesta_utc": "2026-12-27T02:00Z", "proxima_salida_utc": "2026-12-14T23:00Z", "horas_restantes": 335.0}})
    site_id: str = Field(description="Site identifier.")
    desde_utc: UtcStr = Field(description=f"Reference instant ({UTC_NOTE}).")
    sol: BodyEvents = Field(description="Sun set and rise.")
    tierra: BodyEvents = Field(description="Earth set and rise (on the far side it never rises).")

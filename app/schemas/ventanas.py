"""Modelos de respuesta de GET /api/ventanas-alunizaje."""
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UtcStr
from app.schemas.lectura import UTC_NOTE


class LandingWindow(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "llegada_utc": "2026-12-03T10:00Z", "salida_utc": "2026-12-06T10:00Z", "horas_sol": 72.0, "horas_tierra": 40.0,
        "horas_sol_y_tierra": 40.0, "pct_sol": 100.0, "pct_tierra": 55.56, "pct_sol_y_tierra": 55.56,
        "sol_al_llegar": True, "tierra_al_llegar": True, "racha_sin_tierra_h": 32.0}]})

    llegada_utc: UtcStr = Field(description=f"Arrival hour ({UTC_NOTE}). The stay is the interval [llegada_utc, salida_utc).")
    salida_utc: UtcStr = Field(description=f"End of the stay, exclusive ({UTC_NOTE}): arrival plus `estancia_h`.")
    horas_sol: float = Field(description="Hours of the stay with the Sun above the ideal horizon.")
    horas_tierra: float = Field(description="Hours of the stay with the Earth above the horizon (direct link possible).")
    horas_sol_y_tierra: float = Field(description="Hours of the stay with the Sun and the Earth above the horizon at once.")
    pct_sol: float = Field(description="`horas_sol` as a percentage of the stay (0 to 100).")
    pct_tierra: float = Field(description="`horas_tierra` as a percentage of the stay (0 to 100).")
    pct_sol_y_tierra: float = Field(description="`horas_sol_y_tierra` as a percentage of the stay (0 to 100).")
    sol_al_llegar: bool = Field(description="true if the Sun is above the horizon at the arrival hour.")
    tierra_al_llegar: bool = Field(description="true if the Earth is above the horizon at the arrival hour.")
    racha_sin_tierra_h: float = Field(description="Longest continuous stretch without the Earth inside the stay, in hours (0 if it is always visible).")


class DailyBest(BaseModel):
    dia_utc: str = Field(description="UTC day of arrival, `YYYY-MM-DD`.", examples=["2026-12-03"])
    llegada_utc: UtcStr = Field(description=f"Best arrival hour of that day ({UTC_NOTE}): most `horas_sol_y_tierra`, then most `horas_sol`, then the earliest.")
    horas_sol_y_tierra: float = Field(description="Hours with the Sun and the Earth at once if arriving then.")
    pct_sol_y_tierra: float = Field(description="Same, as a percentage of the stay (feeds the heat calendar).")


class SiteWindows(BaseModel):
    site_id: str = Field(description="Site identifier.")
    inicio_utc: UtcStr = Field(description=f"First arrival hour considered ({UTC_NOTE}).")
    fin_utc: UtcStr = Field(description=f"Last ephemeris sample considered, inclusive ({UTC_NOTE}).")
    paso_horas: float = Field(description="Spacing of the ephemerides, in hours.")
    ventanas_evaluadas: int = Field(description="Arrival hours whose whole stay fits in the range.")
    ventanas_descartadas: int = Field(description="Arrival hours dropped because the stay would run past `fin_utc`.")
    trunca: bool = Field(description="true if some arrival hours were dropped because their stay leaves the range of data.")
    serie_diaria: list[DailyBest] = Field(description="Best arrival hour of each UTC day, in chronological order.")
    mejores: list[LandingWindow] = Field(description="Up to 3 non-overlapping windows, best first: most `horas_sol_y_tierra`, then most `horas_sol`, then the earliest.")


class GlobalBest(BaseModel):
    site_id: str = Field(description="Site that holds the best window.")
    ventana: LandingWindow = Field(description="Its best window.")


class LandingWindowsOut(BaseModel):
    estancia_h: int = Field(description="Length of the stay in hours (72, 156 or 336).")
    resultados: list[SiteWindows] = Field(description="One result per requested site, in request order.")
    mejor_global: GlobalBest | None = Field(description="Best window across the requested sites (same order as `mejores`); null when a single site was requested or no window fits.")

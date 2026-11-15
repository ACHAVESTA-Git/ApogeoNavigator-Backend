"""Modelos de respuesta de los endpoints de lectura (con descripciones y ejemplos para /docs)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core import config
from app.schemas.common import UtcStr

Nivel = Literal[*config.SEMAFORO_NIVELES]

UTC_NOTE = "UTC, formato YYYY-MM-DDTHH:MMZ"


def _ejemplo(valor: dict) -> ConfigDict:
    return ConfigDict(json_schema_extra={"examples": [valor]})


class EphemerisRow(BaseModel):
    utc: UtcStr = Field(description=f"Instante de la muestra ({UTC_NOTE}).")
    sol_az_deg: float = Field(description="Azimut del Sol en grados, medido desde el norte en sentido horario (0 a 360).")
    sol_el_deg: float = Field(description="Elevación del Sol sobre el horizonte ideal, en grados (positiva = visible).")
    tierra_az_deg: float = Field(description="Azimut de la Tierra en grados (0 a 360).")
    tierra_el_deg: float = Field(description="Elevación de la Tierra sobre el horizonte ideal, en grados.")


class EphemerisOut(BaseModel):
    model_config = _ejemplo({
        "site_id": "shackleton", "inicio_utc": "2026-12-01T00:00Z", "fin_utc": "2026-12-01T02:00Z", "paso_horas": 1.0,
        "filas": [
            {"utc": "2026-12-01T00:00Z", "sol_az_deg": 150.867622, "sol_el_deg": 1.173151, "tierra_az_deg": 237.476719, "tierra_el_deg": -1.792982},
            {"utc": "2026-12-01T01:00Z", "sol_az_deg": 150.36106, "sol_el_deg": 1.17439, "tierra_az_deg": 237.493202, "tierra_el_deg": -1.856755},
            {"utc": "2026-12-01T02:00Z", "sol_az_deg": 149.854487, "sol_el_deg": 1.175655, "tierra_az_deg": 237.50908, "tierra_el_deg": -1.920325},
        ]})
    site_id: str = Field(description="Identificador del sitio (ver /api/sites).")
    inicio_utc: UtcStr = Field(description=f"Primer instante devuelto ({UTC_NOTE}).")
    fin_utc: UtcStr = Field(description=f"Último instante devuelto, incluido ({UTC_NOTE}).")
    paso_horas: float = Field(description="Separación entre muestras, en horas (1.0 en los datos actuales).")
    filas: list[EphemerisRow] = Field(description="Muestras horarias entre inicio_utc y fin_utc, ambos incluidos.")


class Reference(BaseModel):
    horas_sol: float = Field(description="Horas con el Sol sobre el horizonte según metricas.csv (referencia independiente).")
    pct_sol: float = Field(description="Porcentaje del intervalo con el Sol visible, según la referencia.")
    sol_el_max_deg: float = Field(description="Elevación máxima del Sol en grados, según la referencia.")
    sol_el_media_deg: float | None = Field(description="Elevación media del Sol mientras es visible; null si nunca lo es.")
    horas_tierra_visible: float = Field(description="Horas con la Tierra sobre el horizonte, según la referencia.")
    pct_tierra: float = Field(description="Porcentaje del intervalo con la Tierra visible, según la referencia.")
    coincide: bool = Field(description="true si la referencia coincide con lo recalculado desde las efemérides.")


class Aviso(BaseModel):
    codigo: str = Field(description="Código estable del aviso (hoy solo `sensible_al_relieve`).")
    mensaje: str = Field(description="Texto explicativo para mostrar al usuario.")


class Semaforo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    energia: Nivel = Field(description=(
        f"By % of time with the Sun: verde from {config.SEMAFORO_ENERGIA_VERDE_MIN:g} %, amarillo from "
        f"{config.SEMAFORO_ENERGIA_AMARILLO_MIN:g} % up to {config.SEMAFORO_ENERGIA_VERDE_MIN:g} %, rojo below."))
    comunicacion: Nivel = Field(description=(
        f"By % of time with the Earth: verde from {config.SEMAFORO_COMUNICACION_VERDE_MIN:g} %, amarillo from "
        f"{config.SEMAFORO_COMUNICACION_AMARILLO_MIN:g} % up to {config.SEMAFORO_COMUNICACION_VERDE_MIN:g} %, rojo below."))
    global_: Nivel = Field(alias="global", description="The worse of the two levels above.")


class MetricsItem(BaseModel):
    model_config = _ejemplo({
        "site_id": "shackleton", "inicio_utc": "2026-12-01T00:00Z", "fin_utc": "2027-01-01T00:00Z", "horas_sol": 744.0,
        "pct_sol": 100.0, "sol_el_max_deg": 1.7385, "sol_el_media_deg": 1.2929, "horas_tierra_visible": 291.0, "pct_tierra": 39.11,
        "semaforo": {"energia": "verde", "comunicacion": "rojo", "global": "rojo"},
        "avisos": [{"codigo": "sensible_al_relieve", "mensaje": "Sensible al relieve: el Sol no supera 3° sobre el horizonte ideal, "
                                                                "así que una colina puede taparlo y las horas de sol reales ser menores"}],
        "origen": "calculado",
        "referencia": {"horas_sol": 744.0, "pct_sol": 100.0, "sol_el_max_deg": 1.7385, "sol_el_media_deg": 1.2929,
                       "horas_tierra_visible": 291.0, "pct_tierra": 39.11, "coincide": True}})
    site_id: str = Field(description="Identificador del sitio.")
    inicio_utc: UtcStr = Field(description=f"Inicio del intervalo, incluido ({UTC_NOTE}).")
    fin_utc: UtcStr = Field(description=f"Fin del intervalo, excluido ({UTC_NOTE}).")
    horas_sol: float = Field(description="Horas del intervalo con el Sol sobre el horizonte ideal.")
    pct_sol: float = Field(description="Porcentaje del intervalo con el Sol visible (0 a 100).")
    sol_el_max_deg: float = Field(description="Elevación máxima del Sol en el intervalo, en grados.")
    sol_el_media_deg: float | None = Field(description="Elevación media del Sol mientras es visible; null si nunca lo es.")
    horas_tierra_visible: float = Field(description="Horas del intervalo con la Tierra sobre el horizonte (enlace directo posible).")
    pct_tierra: float = Field(description="Porcentaje del intervalo con la Tierra visible (0 a 100).")
    semaforo: "Semaforo" = Field(description="Nivel verde, amarillo o rojo de `energia` (por % de Sol), `comunicacion` (por % de Tierra) y `global` (el peor).")
    avisos: list[Aviso] = Field(default_factory=list, description="Avisos que acompañan al semáforo; vacío si no hay ninguno.")
    origen: Literal[*config.ORIGENES] = Field(description="`calculado`: recalculado desde las efemérides.")
    referencia: Reference | None = Field(description="Fila de metricas.csv con el mismo sitio y rango; null si no existe o el rango no coincide.")


class MetricsOut(BaseModel):
    resultados: list[MetricsItem] = Field(description="Una métrica por sitio pedido, en el orden de la petición.")


class BodyEvents(BaseModel):
    visible: bool = Field(description="true si el astro está sobre el horizonte en `desde_utc`.")
    proxima_puesta_utc: UtcStr | None = Field(description=f"Próxima puesta del astro ({UTC_NOTE}); null si no hay ninguna en los datos.")
    proxima_salida_utc: UtcStr | None = Field(description=f"Próxima salida del astro ({UTC_NOTE}); null si no hay ninguna en los datos.")
    horas_restantes: float | None = Field(description="Horas hasta el próximo cambio (puesta si es visible, salida si no); null si no hay.")


class SunsetsOut(BaseModel):
    model_config = _ejemplo({
        "site_id": "shackleton", "desde_utc": "2026-12-01T00:00Z",
        "sol": {"visible": True, "proxima_puesta_utc": "2027-01-27T11:00Z", "proxima_salida_utc": "2027-01-29T17:00Z", "horas_restantes": 1379.0},
        "tierra": {"visible": False, "proxima_puesta_utc": "2026-12-27T02:00Z", "proxima_salida_utc": "2026-12-14T23:00Z", "horas_restantes": 335.0}})
    site_id: str = Field(description="Identificador del sitio.")
    desde_utc: UtcStr = Field(description=f"Instante de referencia ({UTC_NOTE}).")
    sol: BodyEvents = Field(description="Puesta y salida del Sol.")
    tierra: BodyEvents = Field(description="Puesta y salida de la Tierra (en la cara oculta nunca sale).")

"""DTO de la sección de transparencia del análisis: cómo se usan los datos de un sitio, con números reales."""
from typing import Literal

from pydantic import BaseModel, Field

from app.core import config as c

Estado = Literal[c.STEP_OK, c.STEP_UNAVAILABLE, c.STEP_WARNING]
OrigenTipo = Literal[c.ORIGIN_NASA_SEED, c.ORIGIN_UPLOADED, c.ORIGIN_MODIFIED, c.ORIGIN_UNRECORDED]
Valor = float | int | str | bool | None


class Medida(BaseModel):
    """Un resultado numérico de un paso, con su etiqueta y su unidad para pintarlo tal cual."""
    nombre: str
    valor: Valor
    unidad: str


class Paso(BaseModel):
    id: str
    nombre: str
    descripcion: str
    estado: Estado
    resultado: list[Medida]


class UltimaCarga(BaseModel):
    cargado_utc: str
    archivo: str
    sha3_256: str
    coincide: bool                    # ¿es el contenido actual del archivo?
    cargado_por: str                  # «clave de API», «usuario registrado» o «anónimo»; nunca identidades


class EntradaManifiesto(BaseModel):
    sha3_256: str
    generado_utc: str
    firmado: bool | None                  # True solo si la firma ML-DSA es VÁLIDA; None si no se pudo verificar; False si no lo es
    firma_nota: str | None                # por qué es False o None
    coincide: bool


class Origen(BaseModel):
    tipo: OrigenTipo
    descripcion: str
    sha3_256: str
    coincide_con_semilla: bool | None    # None si no hay semilla con la que comparar
    ultima_carga: UltimaCarga | None
    manifiesto: EntradaManifiesto | None


class Calidad(BaseModel):
    filas_validas: int
    filas_con_error: int
    huecos: int
    paso_constante: bool


class Entrada(BaseModel):
    archivo: str
    filas: int
    bytes: int
    inicio_utc: str
    fin_utc: str
    paso_horas: float
    calidad: Calidad
    origen: Origen


class Pico(BaseModel):
    periodo_dias: float
    amplitud_rel: float
    candidatos: list[str]
    es_sinodico: bool


class EspectroSerie(BaseModel):
    variable: str
    nombre: str
    picos: list[Pico]


class Espectro(BaseModel):
    ventana_dias: float
    resolucion_dias: float            # P²/T en el mes sinódico: por debajo de esto la FFT no separa dos periodos
    sinodico_teorico_dias: float
    sinodico_afinado_dias: float
    series: list[EspectroSerie]
    nota: str


class Termino(BaseModel):
    cuerpo: str
    periodo_dias: float
    rol: str


class Residuos(BaseModel):
    rmse_deg: float
    mae_deg: float
    sesgo_deg: float
    max_abs_deg: float
    p95_abs_deg: float


class Modelo(BaseModel):
    terminos_por_cuerpo: dict[str, int]
    periodos: list[Termino]
    ajuste_directo_elevacion_sol: bool
    r2: dict[str, float | None] = Field(description=c.NOTE_FIT_IN_SAMPLE_DOC)
    residuos: dict[str, Residuos] = Field(description=c.NOTE_FIT_IN_SAMPLE_DOC)
    nota_ajuste: str                      # texto para pintar junto al R² y los residuos


class Validacion(BaseModel):
    disponible: bool
    vigente: bool | None              # ¿se validó con estos mismos datos? (compara la huella del archivo)
    motivo: str | None
    validacion_desde_utc: str | None
    validacion_hasta_utc: str | None
    filas_entrenamiento_validadas: int | None     # filas con las que se entrenó el modelo que se validó
    rmse_deg: dict[str, float | None]
    mae_deg: dict[str, float | None]
    rmse_referencia_ingenua_deg: dict[str, float | None]
    mejora_frente_a_ingenua: dict[str, float | None]
    rmse_por_mes_deg: dict[str, list[float | None]]
    semaforo_aciertos: dict[str, int]
    puestas_sol: dict[str, float | int | None]


class Limitacion(BaseModel):
    codigo: str
    mensaje: str
    aplica: bool


class PuntoSerie(BaseModel):
    utc: str
    observado: float | None
    ajustado: float | None
    proyectado: float | None


class Serie(BaseModel):
    variable: str
    nombre: str
    paso_horas: float
    fin_datos_utc: str
    puntos: list[PuntoSerie]


class AnalisisOut(BaseModel):
    site_id: str
    desde_utc: str
    hasta_utc: str
    pasos: list[Paso]
    entrada: Entrada
    espectro: Espectro
    modelo: Modelo
    validacion: Validacion
    limitaciones: list[Limitacion]
    serie: Serie

"""Constantes globales del backend.

Fuente de verdad de las reglas: docs/plan/estandar-csv.md (en la raíz del espacio de
trabajo). Si una regla cambia, se cambia primero allí y después aquí. Ningún otro módulo
debe escribir números o cadenas mágicas.
"""
import os
import re
from pathlib import Path

# ---------------------------------------------------------------- entorno
ENV_DATA_DIR = "DATA_DIR"
ENV_UPLOAD_API_KEY = "UPLOAD_API_KEY"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
HISTORY_DIRNAME = "_historial"
API_KEY_HEADER = "X-API-Key"


def get_data_dir() -> Path:
    """Carpeta de datos (se lee en cada llamada para que las pruebas puedan cambiarla)."""
    return Path(os.getenv(ENV_DATA_DIR) or DEFAULT_DATA_DIR)


def get_upload_api_key() -> str:
    return os.getenv(ENV_UPLOAD_API_KEY, "")


# ---------------------------------------------------------------- archivo
CSV_EXTENSION = ".csv"
CSV_ENCODING = "utf-8-sig"          # acepta y elimina el BOM
MAX_FILE_BYTES = 5 * 1024 * 1024    # 5 MB
MAX_DATA_ROWS = 10_000
MAX_COLUMNS = 20
READ_CHUNK_BYTES = 64 * 1024
CSV_DELIMITER = ","
BINARY_MAGIC_PREFIXES = (b"PK\x03\x04", b"%PDF", b"\x89PNG", b"\x1f\x8b", b"\xff\xd8\xff", b"GIF8")
NUL_BYTE = b"\x00"
MAX_REPORTED_ERRORS = 100
MAX_VALUE_CHARS = 50                # recorte de `valor` en el informe
FIRST_DATA_ROW = 2                  # la fila 1 es la cabecera (como en Excel)
HEADER_ROW = 1

# ---------------------------------------------------------------- texto
FORBIDDEN_TEXT_CHARS = "<>"
FORMULA_PREFIXES = ("=", "+", "-", "@")
ALLOWED_CONTROL_CHARS = ("\t",)
CONTROL_CHAR_MAX = 0x1F

# ---------------------------------------------------------------- números
MAX_DECIMALS = 6
DECIMAL_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")   # sin notación científica, inf ni nan

LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 180.0
ALT_KM_MIN, ALT_KM_MAX = -10.0, 11.0
AZ_MIN, AZ_MAX = 0.0, 360.0          # 360 excluido
EL_MIN, EL_MAX = -90.0, 90.0
PCT_MIN, PCT_MAX = 0.0, 100.0

# ---------------------------------------------------------------- fechas
UTC_FORMAT = "%Y-%m-%dT%H:%MZ"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")

# ---------------------------------------------------------------- tipos de archivo
TIPO_SITES = "sites"
TIPO_EFEMERIDES = "efemerides"
TIPO_METRICAS = "metricas"
TIPOS_ARCHIVO = (TIPO_SITES, TIPO_EFEMERIDES, TIPO_METRICAS)

SITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,39}$")
SITE_ID_PATTERN = SITE_ID_RE.pattern
FILENAME_PATTERNS = {
    TIPO_SITES: re.compile(r"^sites\.csv$"),
    TIPO_EFEMERIDES: re.compile(r"^efemerides_([a-z0-9][a-z0-9_-]{2,39})\.csv$"),
    TIPO_METRICAS: re.compile(r"^metricas\.csv$"),
}
SITES_FILENAME = "sites.csv"
HTTPS_URL_RE = re.compile(r"^https://\S+$")

SITE_CATEGORIAS = ("apolo", "artemis", "clps", "internacional", "historico", "personalizado")
CATEGORIA_ARTEMIS = "artemis"
ARTEMIS_MAX_LAT = -80.0               # una región Artemis más al norte es sospechosa

# ---------------------------------------------------------------- coherencia
COORD_DUPLICATE_TOL_DEG = 0.0001

EPHEM_MIN_ROWS = 24
EPHEM_MAX_RANGE_DAYS = 366
EPHEM_MIN_STEP_MINUTES = 10
EPHEM_MAX_STEP_MINUTES = 24 * 60
EPHEM_MAX_ELEVATION_RATE_DEG_H = 1.0   # |Δ elevación| por hora, Sol y Tierra

METRICS_MAX_RANGE_DAYS = 35
METRICS_PCT_TOL = 0.5
HOURS_TOL = 1e-6

# ---------------------------------------------------------------- códigos de error
E_ARCHIVO_VACIO = "E_ARCHIVO_VACIO"
E_TAMANO = "E_TAMANO"
E_TIPO_ARCHIVO = "E_TIPO_ARCHIVO"
E_CODIFICACION = "E_CODIFICACION"
E_NOMBRE_ARCHIVO = "E_NOMBRE_ARCHIVO"
E_CABECERA = "E_CABECERA"
E_COLUMNA_DESCONOCIDA = "E_COLUMNA_DESCONOCIDA"
E_NUM_COLUMNAS = "E_NUM_COLUMNAS"
E_REQUERIDO = "E_REQUERIDO"
E_TIPO = "E_TIPO"
E_FORMATO = "E_FORMATO"
E_LONGITUD = "E_LONGITUD"
E_RANGO = "E_RANGO"
E_DECIMALES = "E_DECIMALES"
E_ENUM = "E_ENUM"
E_CARACTER = "E_CARACTER"
E_DUPLICADO = "E_DUPLICADO"
E_ORDEN = "E_ORDEN"
E_PASO = "E_PASO"
E_SALTO_FISICO = "E_SALTO_FISICO"
E_REFERENCIA = "E_REFERENCIA"
E_COHERENCIA = "E_COHERENCIA"
W_COORD_DUPLICADA = "W_COORD_DUPLICADA"
W_ARTEMIS_LATITUD = "W_ARTEMIS_LATITUD"

HTTP_BY_CODE = {E_TAMANO: 413, E_TIPO_ARCHIVO: 415}
HTTP_DEFAULT_ERROR = 422
HTTP_UNAUTHORIZED = 401

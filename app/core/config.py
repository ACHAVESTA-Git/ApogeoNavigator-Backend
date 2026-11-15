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
ENV_ADMIN_API_KEY = "ADMIN_API_KEY"
ENV_DATABASE_URL = "DATABASE_URL"
ENV_DATA_BUCKET = "DATA_BUCKET"
ENV_DATA_BUCKET_PREFIX = "DATA_BUCKET_PREFIX"
ENV_DATA_SEED_DIR = "DATA_SEED_DIR"
ENV_DATA_REFRESH_SECONDS = "DATA_REFRESH_SECONDS"
ENV_IP_HASH_SALT = "IP_HASH_SALT"
ENV_AUDIT_REQUIRE_SIGNATURE = "AUDIT_REQUIRE_SIGNATURE"
ENV_CLOUD_RUN_MARKER = "K_SERVICE"
ENV_ENVIRONMENT = "ENVIRONMENT"
ENVIRONMENT_PRODUCTION = "production"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
HISTORY_DIRNAME = "_historial"
HISTORY_STAMP = "%Y%m%dT%H%M%S%fZ"
FILE_MODE = 0o644
TMP_PREFIX = ".tmp_"
TMP_SUFFIX = ".tmp"
API_KEY_HEADER = "X-API-Key"
UPLOAD_ROUTER_PREFIX = "/api/datos"
MULTIPART_OVERHEAD_BYTES = 64 * 1024
CONTENT_LENGTH_HEADER = "content-length"
KEY_ENCODING = "utf-8"
HEADER_ENCODING = "latin-1"


def get_data_dir() -> Path:
    """Carpeta de datos (se lee en cada llamada para que las pruebas puedan cambiarla)."""
    return Path(os.getenv(ENV_DATA_DIR) or DEFAULT_DATA_DIR)


def get_data_bucket() -> str:
    """Bucket de Cloud Storage con los datos. Vacío = solo disco local (desarrollo)."""
    return os.getenv(ENV_DATA_BUCKET, "").strip()


def get_data_bucket_prefix() -> str:
    prefix = os.getenv(ENV_DATA_BUCKET_PREFIX, "").strip().strip("/")
    return f"{prefix}/" if prefix else ""


def get_seed_dir() -> Path | None:
    """Carpeta con la semilla de datos (la imagen Docker la copia a /srv/seed-data)."""
    raw = os.getenv(ENV_DATA_SEED_DIR)
    return Path(raw) if raw else None


def get_data_refresh_seconds() -> float:
    try:
        return max(0.0, float(os.getenv(ENV_DATA_REFRESH_SECONDS, DEFAULT_DATA_REFRESH_SECONDS)))
    except ValueError:
        return DEFAULT_DATA_REFRESH_SECONDS


def get_database_url() -> str:
    """URL de SQLAlchemy (p. ej. postgresql+psycopg://...). Vacía = auditoría y BD desactivadas."""
    return os.getenv(ENV_DATABASE_URL, "")


def get_ip_hash_salt() -> str:
    return os.getenv(ENV_IP_HASH_SALT, "")


def is_production() -> bool:
    """Cloud Run (K_SERVICE definido) o ENVIRONMENT=production."""
    return bool(os.getenv(ENV_CLOUD_RUN_MARKER)) or os.getenv(ENV_ENVIRONMENT, "").strip().lower() == ENVIRONMENT_PRODUCTION


def audit_requires_signature() -> bool:
    """En producción (o con AUDIT_REQUIRE_SIGNATURE=1) un evento que no se puede firmar no se registra: falla cerrado."""
    return is_production() or os.getenv(ENV_AUDIT_REQUIRE_SIGNATURE, "").lower() in TRUE_VALUES


def get_upload_api_key() -> str:
    return os.getenv(ENV_UPLOAD_API_KEY, "")


def get_admin_api_key() -> str:
    """Clave del gestor de claves (X-API-Key). Debe ser distinta de UPLOAD_API_KEY; vacía = gestor cerrado."""
    return os.getenv(ENV_ADMIN_API_KEY, "")


TRUE_VALUES = ("1", "true", "yes", "si")

# ---------------------------------------------------------------- archivo
CSV_EXTENSION = ".csv"
CSV_ENCODING = "utf-8-sig"          # acepta y elimina el BOM
BYTES_PER_MB = 1024 * 1024
MAX_FILE_MB = 5
MAX_FILE_BYTES = MAX_FILE_MB * BYTES_PER_MB
MAX_DATA_ROWS = 10_000
ROW_READ_SLACK = 3                  # cabecera + línea final en blanco + 1 fila para detectar exceso
READ_CHUNK_BYTES = 64 * 1024
CSV_DELIMITER = ","
BINARY_MAGIC_PREFIXES = (b"PK\x03\x04", b"%PDF", b"\x89PNG", b"\x1f\x8b", b"\xff\xd8\xff", b"GIF8")
NUL_BYTE = b"\x00"
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
UTF8_BOM = b"\xef\xbb\xbf"
MAX_REPORTED_ERRORS = 100
MAX_VALUE_CHARS = 50                # recorte de `valor` en el informe
MAX_FILENAME_CHARS = 120            # recorte de `archivo` en el informe
HEADER_ROW = 1

# ---------------------------------------------------------------- texto
NULL_TOKENS = ("NULL", "NAN", "N/A", "-")   # nulos disfrazados (se comparan en mayúsculas)
FORBIDDEN_TEXT_CHARS = "<>"
FORMULA_PREFIXES = ("=", "+", "-", "@")
ALLOWED_CONTROL_CHARS = ("\t",)
CONTROL_CHAR_MAX = 0x1F

# ---------------------------------------------------------------- números
MAX_DECIMALS = 6
DECIMAL_RE = re.compile(r"[+-]?[0-9]+(\.[0-9]+)?")   # se usa con fullmatch; sin notación científica, inf ni nan

LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 180.0
ALT_KM_MIN, ALT_KM_MAX = -10.0, 11.0
AZ_MIN, AZ_MAX = 0.0, 360.0          # 360 excluido
EL_MIN, EL_MAX = -90.0, 90.0
PCT_MIN, PCT_MAX = 0.0, 100.0

# ---------------------------------------------------------------- fechas
UTC_FORMAT = "%Y-%m-%dT%H:%MZ"
UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z")   # fullmatch

# ---------------------------------------------------------------- tipos de archivo
TIPO_SITES = "sites"
TIPO_EFEMERIDES = "efemerides"
TIPO_METRICAS = "metricas"
TIPOS_ARCHIVO = (TIPO_SITES, TIPO_EFEMERIDES, TIPO_METRICAS)

SITE_ID_CORE = r"[a-z0-9][a-z0-9_-]{2,39}"
SITE_ID_RE = re.compile(SITE_ID_CORE)    # fullmatch
FILENAME_PATTERNS = {
    TIPO_SITES: re.compile(r"sites\.csv"),
    TIPO_EFEMERIDES: re.compile(rf"efemerides_({SITE_ID_CORE})\.csv"),
    TIPO_METRICAS: re.compile(r"metricas\.csv"),
}
SITES_FILENAME = "sites.csv"
HTTPS_URL_RE = re.compile(r"https://\S+")   # fullmatch

# Longitudes (mín, máx) de las columnas de texto
LEN_NOMBRE = (3, 120)
LEN_MISION = (2, 80)
LEN_AGENCIA = (2, 40)
LEN_URL = (10, 300)
LEN_NOTAS = (0, 500)
ALT_KM_DEFAULT = 0.0                   # alt_km vacío = 0

# Nombres de columna (estándar §2-§4)
COL_SITE_ID = "site_id"
COL_NOMBRE = "nombre"
COL_CATEGORIA = "categoria"
COL_MISION = "mision"
COL_AGENCIA = "agencia"
COL_LAT = "lat_deg"
COL_LON = "lon_este_deg"
COL_ALT = "alt_km"
COL_FUENTE = "fuente_url"
COL_NOTAS = "notas"
COL_UTC = "utc"
COL_SOL_AZ = "sol_az_deg"
COL_SOL_EL = "sol_el_deg"
COL_TIERRA_AZ = "tierra_az_deg"
COL_TIERRA_EL = "tierra_el_deg"
COL_INICIO = "inicio_utc"
COL_FIN = "fin_utc"
COL_HORAS_SOL = "horas_sol"
COL_PCT_SOL = "pct_sol"
COL_SOL_EL_MAX = "sol_el_max_deg"
COL_SOL_EL_MEDIA = "sol_el_media_deg"
COL_HORAS_TIERRA = "horas_tierra_visible"
COL_PCT_TIERRA = "pct_tierra"
COL_ARCHIVO = "archivo"

# Columnas exactas y en orden
SITES_COLUMNS = (COL_SITE_ID, COL_NOMBRE, COL_CATEGORIA, COL_MISION, COL_AGENCIA, COL_LAT, COL_LON, COL_ALT,
                 COL_FUENTE, COL_NOTAS)
EPHEM_COLUMNS = (COL_UTC, COL_SOL_AZ, COL_SOL_EL, COL_TIERRA_AZ, COL_TIERRA_EL)
METRICS_COLUMNS = (COL_SITE_ID, COL_INICIO, COL_FIN, COL_HORAS_SOL, COL_PCT_SOL, COL_SOL_EL_MAX,
                   COL_SOL_EL_MEDIA, COL_HORAS_TIERRA, COL_PCT_TIERRA)

SITE_CATEGORIAS = ("apolo", "artemis", "clps", "internacional", "historico", "personalizado")
CATEGORIA_ARTEMIS = "artemis"
ARTEMIS_MAX_LAT = -80.0               # una región Artemis más al norte es sospechosa

# ---------------------------------------------------------------- tiempo
MINUTES_PER_DAY = 24 * 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60
HOURS_PER_DAY = 24

# ---------------------------------------------------------------- coherencia
COORD_DUPLICATE_TOL_DEG = 0.0001

EPHEM_MIN_ROWS = 24
EPHEM_MAX_RANGE_DAYS = 366
EPHEM_MIN_STEP_MINUTES = 10
EPHEM_MAX_STEP_MINUTES = MINUTES_PER_DAY
EPHEM_REFERENCE_STEP_MINUTES = 60
EPHEM_MAX_ELEVATION_RATE_DEG_H = 1.0   # |Δ elevación| por hora, Sol y Tierra

METRICS_MAX_RANGE_DAYS = 35
METRICS_PCT_TOL = 0.5
PCT_FULL_SCALE = 100
ELEVATION_RATE_TOL = 1e-9
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
HTTP_UNPROCESSABLE = 422
HTTP_DEFAULT_ERROR = HTTP_UNPROCESSABLE
HTTP_UNAUTHORIZED = 401
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_UNAVAILABLE = 503
HTTP_INTERNAL_ERROR = 500
HTTP_CREATED = 201
HTTP_CONFLICT = 409

# ---------------------------------------------------------------- mensajes (plantillas str.format)
MSG_CONTROL_CHAR = "Contiene un caracter de control"
MSG_FORBIDDEN_CHAR = "Caracter no permitido: {char}"
MSG_FORMULA = "No puede empezar por = + - @ (posible fórmula)"
MSG_LENGTH = "Debe tener entre {lo} y {hi} caracteres"
MSG_NOT_NUMBER = "No es un número decimal (sin notación científica, inf ni nan)"
MSG_DECIMALS = "Máximo {max} decimales"
MSG_RANGE = "Debe estar entre {lo:g} y {hi:g}"
MSG_RANGE_EXCLUDED = "Debe estar entre {lo:g} y {hi:g} (excluido)"
MSG_RANGE_HOURS = "Debe estar entre 0 y {hi:g} (horas del rango)"
MSG_DATE_FORMAT = "Formato esperado: YYYY-MM-DDTHH:MMZ (UTC)"
MSG_DATE_INVALID = "No es una fecha válida"
MSG_ENUM = "Valores permitidos: {values}"
MSG_REGEX = "No cumple el formato {pattern}"
MSG_NULL_TOKEN = "Nulo disfrazado: deja el campo vacío"
MSG_REQUIRED = "Campo obligatorio vacío"
MSG_DUPLICATE_VALUE = "Valor repetido"
MSG_BAD_EXTENSION = "La extensión debe ser {ext}"
MSG_TOO_BIG = "Máximo {mb} MB"
MSG_TOO_MANY_ROWS = "Máximo {max} filas de datos"
MSG_BINARY = "El contenido es binario, no texto CSV"
MSG_EMPTY = "El archivo está vacío"
MSG_ONLY_HEADER = "El archivo solo tiene cabecera"
MSG_ENCODING = "No es UTF-8 válido"
MSG_BAD_NAME = "El nombre debe cumplir {pattern}"
MSG_BAD_CSV = "CSV mal formado: {detail}"
MSG_HEADER_DUPLICATE = "Columna duplicada"
MSG_HEADER_UNKNOWN = "Columna que no existe en el estándar"
MSG_HEADER_MISSING = "Falta la columna obligatoria"
MSG_HEADER_ORDER = "Orden incorrecto; esperado: {expected}"
MSG_ROW_EMPTY = "Fila vacía"
MSG_ROW_WIDTH = "Tiene {found} campos; se esperaban {expected}"
MSG_API_KEY_INVALID = "API key inválida o ausente"
MSG_UNKNOWN_TIPO = "Tipo desconocido; usa uno de {tipos}"
MSG_NO_SITES = "No se encontró sites.csv"
MSG_SITES_INVALID = "sites.csv no cumple el estándar de CSV"
MSG_NO_SITES_FILE = "No existe sites.csv: cárgalo primero"
MSG_SITE_NOT_FOUND = "El site_id no está en sites.csv"
MSG_ARTEMIS_LAT = "Una región Artemis debería estar al sur de {lat:g}°"
MSG_COORD_DUPLICATE = "Coordenadas casi idénticas a otro sitio"
MSG_EPHEM_MIN_ROWS = "Se necesitan al menos {min} filas"
MSG_UTC_DUPLICATE = "utc repetido"
MSG_UTC_ORDER = "utc debe ser estrictamente creciente"
MSG_STEP_RANGE = "El paso debe estar entre {min} min y 24 h"
MSG_STEP_CONSTANT = "El paso temporal no es constante"
MSG_RANGE_DAYS = "El rango máximo es {max} días"
MSG_PHYSICAL_JUMP = "Variación de {rate:.2f}°/h; el máximo es {max:g}°/h"
MSG_METRICS_DUPLICATE = "Fila repetida (site_id, inicio_utc, fin_utc)"
MSG_END_BEFORE_START = "fin_utc debe ser posterior a inicio_utc"
MSG_PCT_MISMATCH = "No coincide con {hours} / horas del rango × 100"
MSG_MEAN_GT_MAX = "La media no puede superar al máximo"
MSG_MEAN_NOT_EMPTY = "Debe ir vacía si horas_sol = 0"

# ---------------------------------------------------------------- aplicación
API_TITLE = "Apogeo API"
API_VERSION = "0.2.0"
ROUTE_HEALTH = "/api/health"
ROUTE_SITES = "/api/sites"
ROUTER_TAG = "datos"
HEALTH_OK = {"status": "ok"}

# ---------------------------------------------------------------- lectura (endpoints de consulta)
ROUTER_TAG_LECTURA = "lectura"
EFEMERIDES_FILENAME = "efemerides_{site_id}.csv"
METRICS_FILENAME = "metricas.csv"
QUERY_MAX_RANGE_DAYS = 35
MAX_COMPARE_SITES = 3
ORIGEN_CALCULADO = "calculado"
# Umbrales del semáforo, fijados por el usuario (% de tiempo con el astro sobre el horizonte).
# Energía (Sol): verde >= 45, amarillo de 25 a < 45, rojo < 25. Comunicación (Tierra): verde >= 70, amarillo de 40 a < 70.
SEMAFORO_ENERGIA_VERDE_MIN = 45.0
SEMAFORO_ENERGIA_AMARILLO_MIN = 25.0
SEMAFORO_COMUNICACION_VERDE_MIN = 70.0
SEMAFORO_COMUNICACION_AMARILLO_MIN = 40.0
RELIEF_WARNING_MAX_SUN_EL_DEG = 3.0   # Sol siempre por debajo de esto: el relieve real puede taparlo
AVISO_RELIEVE = "sensible_al_relieve"
MSG_AVISO_RELIEVE = ("Sensible al relieve: el Sol no supera {deg:g}° sobre el horizonte ideal, "
                     "así que una colina puede taparlo y las horas de sol reales ser menores")
KEY_AVISOS = "avisos"
KEY_CODIGO = "codigo"
KEY_MENSAJE = "mensaje"
SEMAFORO_VERDE, SEMAFORO_AMARILLO, SEMAFORO_ROJO = "verde", "amarillo", "rojo"
METRICS_DEG_TOL = 0.001
MSG_SITE_NOT_IN_CATALOG = "El sitio no existe en sites.csv"
MSG_NO_EPHEMERIS = "El sitio no tiene efemérides cargadas"
MSG_EPHEM_INVALID = "El archivo de efemérides no cumple el estándar de CSV"
MSG_RANGE_ORDER = "'hasta' debe ser posterior a 'desde'"
MSG_QUERY_RANGE = "El rango máximo es de {max} días (se pidieron {days:.2f} días)"
MSG_OUT_OF_DATA = "'{param}' está fuera del rango de datos disponible"
MSG_COMPARE_COUNT = "Se pueden consultar de 1 a {max} sitios"
MSG_SITE_ID_REQUIRED = "Indica al menos un site_id"
MSG_NO_ROWS_IN_RANGE = "No hay datos en el rango pedido"
API_PREFIX = "/api"
ROUTE_EFEMERIDES = "/efemerides/{site_id}"
ROUTE_METRICAS = "/metricas"
ROUTE_PUESTAS = "/puestas/{site_id}"
PARAM_DESDE = "desde"
PARAM_HASTA = "hasta"
ROUND_PCT = 2
ROUND_DEG = 4
ROUND_HOURS = 2
DESC_NOT_FOUND = "El sitio no existe o no tiene efemérides"
DESC_UNPROCESSABLE = "Rango o parámetros no válidos"
DESC_UNAVAILABLE = "Faltan datos o no cumplen el estándar de CSV"
KEY_PASO_HORAS = "paso_horas"
KEY_FILAS = "filas"
KEY_RESULTADOS = "resultados"
KEY_SEMAFORO = "semaforo"
KEY_ENERGIA = "energia"
KEY_COMUNICACION = "comunicacion"
KEY_GLOBAL = "global"
KEY_ORIGEN = "origen"
KEY_REFERENCIA = "referencia"
KEY_COINCIDE = "coincide"
KEY_DESDE_UTC = "desde_utc"
KEY_SOL = "sol"
KEY_TIERRA = "tierra"
KEY_VISIBLE = "visible"
KEY_PUESTA = "proxima_puesta_utc"
KEY_SALIDA = "proxima_salida_utc"
KEY_HORAS_RESTANTES = "horas_restantes"
MSG_METRICS_INVALID = "metricas.csv no cumple el estándar de CSV"

# ---------------------------------------------------------------- base de datos y auditoría
DB_POOL_RECYCLE_SECONDS = 1800
DB_POOL_SIZE = 2                      # pequeño: Cloud Run escala instancias y Cloud SQL limita conexiones
DB_MAX_OVERFLOW = 2
DB_POOL_TIMEOUT_SECONDS = 10
DB_SQLITE_PREFIX = "sqlite"
DB_PSYCOPG_PREFIX = "postgresql+psycopg://"      # no casa con psycopg2, que no admite prepare_threshold
DB_EMAIL_MAX = 320
DB_NAME_MAX = 120
DB_PROVIDER_MAX = 40
DB_SUB_MAX = 255
DB_ACTOR_MAX = 80
DB_FILENAME_MAX = 120
DB_RESOURCE_MAX = 200
DB_PATH_MAX = 500
DB_USER_AGENT_MAX = 200
DB_ALGORITHM_MAX = 40
DB_KEY_FINGERPRINT_MAX = 64
SIGNED_DOC_VERSION = 1
AUDIT_ADVISORY_LOCK_KEY = 0x41504F47        # "APOG": serializa la cadena en PostgreSQL
AUDIT_DETAIL_MAX_BYTES = 4096
KEY_TRUNCADO = "truncado"             # detalle del evento: el archivo se cortó al pasar el límite de tamaño
AUDIT_FIRST_SEQUENCE = 1
CK_SIGNED_OR_NONE = "ck_{tabla}_firma_o_ninguno"
HASH_HEX_LENGTH = 64                  # SHA3-256 / HMAC-SHA256 en hexadecimal

ACTOR_API_KEY = "api-key"
ACTOR_ANONYMOUS = "anonimo"
GENESIS_HASH = "0" * HASH_HEX_LENGTH  # hash_prev del primer evento
AUDIT_SIGN_CONTEXT = b"auditoria"
UPLOAD_SIGN_CONTEXT = b"carga-csv"
AUDIT_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
AUDIT_WRITE_RETRIES = 3
AUDIT_VERIFY_BATCH = 500
AUDIT_PAGE_DEFAULT = 50
AUDIT_PAGE_MAX = 200
FORWARDED_FOR_HEADER = "x-forwarded-for"
USER_AGENT_HEADER = "user-agent"
UNSIGNED_ALGORITHM = "NINGUNO"

ROUTE_SECURITY_PUBKEY = "/api/seguridad/clave-publica"
ROUTE_SECURITY_MANIFEST = "/api/seguridad/manifiesto"
ROUTER_TAG_SECURITY = "seguridad"
CACHE_CONTROL_HEADER = "Cache-Control"
CACHE_CONTROL_NO_CACHE = "no-cache"
JSON_MEDIA_TYPE = "application/json"
MSG_NO_MANIFEST = "Aún no hay manifiesto firmado de los datos"
MSG_SIGNING_UNAVAILABLE = "La firma post-cuántica no está disponible; no se guardó nada"
MSG_AUDIT_UNAVAILABLE = "No se pudo registrar la auditoría; no se guardó nada"
ROUTE_AUDIT = "/api/auditoria"
ROUTE_AUDIT_VERIFY = "/api/auditoria/verificar"
ROUTER_TAG_AUDIT = "auditoria"
MSG_DB_DISABLED = "La base de datos no está configurada (DATABASE_URL)"
MSG_AUDIT_RANGE_ORDER = "'hasta' debe ser posterior a 'desde'"
MSG_AUDIT_WRITE_FAILED = "No se pudo registrar el evento de auditoría"
MSG_AUDIT_NO_SIGNER = "Firma PQC no disponible; el evento se guarda sin firmar"
MSG_AUDIT_BROKEN_LINK = "La cadena está rota: hash_prev no coincide con el evento anterior"
MSG_AUDIT_BAD_HASH = "hash_actual no coincide: el evento fue alterado"
MSG_AUDIT_BAD_SIGNATURE = "La firma del evento no es válida"
MSG_AUDIT_ALGORITHM_MISMATCH = "El algoritmo de firma del evento no coincide con el configurado"
MSG_AUDIT_DETAIL_TOO_BIG = "El detalle del evento supera {max} bytes"
MSG_AUDIT_BAD_SEQUENCE = "La secuencia no es consecutiva: falta o sobra un evento"
MSG_AUDIT_SALT_REQUIRED = "En producción IP_HASH_SALT es obligatoria cuando hay base de datos"
MSG_AUDIT_DETAIL_TYPE = "El detalle solo admite str, int, bool, None, listas y dicts con claves de texto"
MSG_AUDIT_ANCHOR = "auditoria cabeza secuencia=%s hash=%s"
MSG_AUDIT_UNSIGNED = "El evento no está firmado (algoritmo NINGUNO): la cadena no es verificable"
MSG_AUDIT_KEY_MISMATCH = "El evento se firmó con otra clave (huella distinta de la configurada)"
MSG_AUDIT_CANNOT_VERIFY = "Hay eventos firmados pero no hay verificador de firmas disponible"
MSG_AUDIT_SIGNATURE_REQUIRED = "No se pudo firmar el evento y la firma es obligatoria"

# ---------------------------------------------------------------- almacenamiento en Cloud Storage
DEFAULT_DATA_REFRESH_SECONDS = 30.0
BUCKET_HISTORY_PREFIX = f"{HISTORY_DIRNAME}/"
NEW_OBJECT_GENERATION = 0               # if_generation_match=0: solo crear si el objeto no existe
MANIFEST_NAME = "manifest.json"
BUCKET_PATH_SEP = "/"
LOGGER_DATASTORE = "apogeo.datastore"
MSG_REPLICA_RESET_FAILED = "No se pudo restablecer la réplica local desde el bucket (%s)"
SEED_SUFFIX = ".csv"
MSG_PROD_NEEDS_BUCKET = ("En producción hay que configurar DATA_BUCKET: el disco de Cloud Run es efímero y los datos "
                         "cargados se perderían. Define DATA_BUCKET (y dale al servicio permiso sobre el bucket)")
MSG_DB_UNAVAILABLE = "La base de datos no está disponible; inténtalo de nuevo en unos minutos"
MSG_INTERNAL_ERROR = "Error interno"
LOGGER_ERRORS = "apogeo.errores"
MSG_UNHANDLED_LOG = "Error no controlado en %s %s"
MSG_DB_ERROR_LOG = "Error de base de datos en %s %s (%s)"
UTC_FIELD_PATTERN = rf"^{UTC_RE.pattern}$"      # patrón anclado para campos UTC de los DTO
MSG_BUCKET_UNAVAILABLE = "No se pudo acceder al almacenamiento de datos; no se guardó nada"
MSG_DATA_CONFLICT = "Otro proceso modificó los datos a la vez; se recargó la versión vigente. Reintenta la carga"
MSG_SEED_MANIFEST_SKIPPED = "No se pudo firmar el manifiesto de la semilla (%s); se publicará con la primera carga"
HTTP_CONFLICT = 409

# ---------------------------------------------------------------- gestor de claves PQC (app/security/gestor_claves.py)
# Por usuario: un par ML-KEM-1024 (FIPS 203) y un par ML-DSA-65 (FIPS 204). La privada no se guarda nunca.
ALG_KEM = "ML-KEM-1024"
ALG_FIRMA = "ML-DSA-65"
TIPO_KEM = "kem"
TIPO_FIRMA = "firma"
ALGORITMO_POR_TIPO = {TIPO_KEM: ALG_KEM, TIPO_FIRMA: ALG_FIRMA}
# Tamaños según FIPS 203 (ML-KEM-1024) y FIPS 204 (ML-DSA-65). La privada se entrega como semilla (64 y 32 bytes).
TAMANO_PUBLICA = {TIPO_KEM: 1568, TIPO_FIRMA: 1952}
TAMANO_PRIVADA_ENTREGADA = {TIPO_KEM: 64, TIPO_FIRMA: 32}
TAMANO_PRIVADA_FIPS = {TIPO_KEM: 3168, TIPO_FIRMA: 4032}
KEM_CIPHERTEXT_BYTES = 1568
KEM_SEED_BYTES = 64
SHARED_SECRET_BYTES = 32

KEM_DEM_VERSION = b"\x02"
KEM_DEM_NONCE_BYTES = 12
KEY_ID_BYTES = 8
KEM_DEM_TAG_BYTES = 16
KEM_DEM_HEADER_BYTES = 1 + KEY_ID_BYTES + KEM_CIPHERTEXT_BYTES + KEM_DEM_NONCE_BYTES
KEM_DEM_INFO = b"apogeo-kem-dem-v1"
EMAIL_AAD = b"usuarios.email"

ENV_KEM_PRIVATE = "PQC_KEM_PRIVATE_KEY"
ENV_KEM_PUBLIC = "PQC_KEM_PUBLIC_KEY"
ENV_EMAIL_HASH_KEY = "EMAIL_HASH_KEY"
ENV_KEM_PREVIOUS = "PQC_KEM_PRIVATE_KEY_ANTERIORES"      # semillas anteriores, separadas por comas
ENV_EMAIL_HASH_PREVIOUS = "EMAIL_HASH_KEY_ANTERIORES"     # claves de hash anteriores, separadas por comas
LIST_SEPARATOR = ","
DEV_SERVER_KEYS_FILENAME = "claves_servidor.json"
EMAIL_HASH_KEY_BYTES = 32

MSG_TIPO_DESCONOCIDO = "Tipo de clave desconocido: {tipo!r}."
MSG_NO_SERVER_KEYS = (
    f"Faltan {ENV_KEM_PRIVATE} o {ENV_EMAIL_HASH_KEY} y el entorno es de producción: "
    "no se generan claves del servidor automáticamente."
)
MSG_SERVER_KEYS_DEV = "Claves del servidor (KEM y hash de email) de DESARROLLO en {ruta}; no valen para producción."
MSG_BAD_BLOB = "Dato cifrado inválido o manipulado."
MSG_UNKNOWN_KEY = "El dato está cifrado con una clave del servidor que ya no está configurada."
MSG_BAD_KEY = "{nombre} inválida: {detalle}."


# ---------------------------------------------------------------- admin de claves (app/routers/admin_claves.py)
KEYS_ROUTE_PREFIX = "/api/admin/claves"
KEYS_ROUTER_TAG = "admin-claves"
ACTOR_ADMIN = "admin-api-key"
KEYS_PAGE_DEFAULT = 50
KEYS_PAGE_MAX = 200
KEYS_Q_MAX = 100
KEYS_MOTIVE_MIN = 3
KEYS_MOTIVE_MAX = 200
KEYS_ALGORITHM_MAX = 20
KEYS_MOTIVE_ROTATION = "rotación"
LIKE_ESCAPE = "\\"
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
PEM_MEDIA_TYPE = "application/x-pem-file"
KEYS_FORMAT_PEM = "pem"
KEYS_FORMAT_JSON = "json"
KEYS_FORMAT_PATTERN = f"^({KEYS_FORMAT_JSON}|{KEYS_FORMAT_PEM})$"
KEYS_FILE_FINGERPRINT_CHARS = 12
KEYS_PRIVATE_FILENAME = "{tipo}-{huella}.privada.pem"
KEYS_PUBLIC_FILENAME = "{tipo}-{huella}.publica.pem"
KEYS_AUDIT_RESOURCE = "clave/{id}"
KEYS_LOGGER = "apogeo.claves"
OP_KEY_GENERATED = "clave_generada"
OP_KEY_REVOKED = "clave_revocada"
OP_KEY_ROTATED = "clave_rotada"
OP_KEY_READ = "lectura"
MSG_KEYS_ADMIN_KEY = "Clave de administración inválida o ausente"
MSG_KEYS_NO_USER = "El usuario no existe"
MSG_KEYS_INACTIVE_USER = "El usuario está desactivado; no se le pueden emitir claves"
MSG_KEYS_NO_KEY = "La clave no existe"
MSG_KEYS_ALREADY_ACTIVE = "El usuario ya tiene una clave activa de tipo {tipo}; usa rotar para reemitirla"
MSG_KEYS_ALREADY_ACTIVE_GENERIC = "Ya hay una clave activa para ese usuario y tipo"
MSG_KEYS_ALREADY_REVOKED = "La clave ya está revocada"
MSG_KEYS_AUDIT_FAILED = "No se pudo registrar la auditoría; no se hizo ningún cambio"
MSG_KEYS_WARNING = ("Guarda la clave privada ahora: se muestra UNA sola vez y no se puede recuperar. "
                    "Si se pierde, hay que revocar el par y emitir uno nuevo.")
MSG_KEYS_DELIVERY_COUNT_FAILED = "no se pudo contar la entrega de la clave (%s)"
MSG_KEYS_SAME_KEY_LOG = "ADMIN_API_KEY es igual a UPLOAD_API_KEY: el gestor de claves queda cerrado"
MSG_KEYS_READ_LOG = "lectura de admin: operacion=%s filtros=%s resultados=%s"

# ---------------------------------------------------------------- CORS
ENV_CORS_ORIGINS = "CORS_ORIGINS"                 # lista separada por comas
ENV_CORS_ORIGIN_REGEX = "CORS_ORIGIN_REGEX"       # p. ej. ^https://([a-z0-9-]+\.)?mi-proyecto\.pages\.dev$
CORS_DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
CORS_ALLOW_METHODS = ("GET", "POST", "OPTIONS")
CORS_ALLOW_HEADERS = (API_KEY_HEADER, "Content-Type")
CORS_ALLOW_CREDENTIALS = False                    # nunca credenciales con orígenes comodín
CORS_MAX_AGE_SECONDS = 600
CORS_WILDCARD = "*"
MSG_CORS_WILDCARD = "CORS_ORIGINS no admite «*»: lista los orígenes permitidos"


def get_cors_origins() -> list[str]:
    """Orígenes permitidos: CORS_ORIGINS (separados por comas) o, sin definir, los de desarrollo local."""
    raw = os.getenv(ENV_CORS_ORIGINS)
    if raw is None:
        return list(CORS_DEFAULT_ORIGINS)
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if CORS_WILDCARD in origins:
        raise ValueError(MSG_CORS_WILDCARD)
    return origins


def get_cors_origin_regex() -> str | None:
    return os.getenv(ENV_CORS_ORIGIN_REGEX) or None

# ---------------------------------------------------------------- análisis y proyección (Polars + numpy)
# Periodos físicos de la Luna (días). La FFT de 6 meses no separa el anomalístico del dracónico (resolución
# P²/T ≈ 4 d), así que el modelo los toma como constantes y la FFT solo los confirma y afina el sinódico.
SYNODIC_MONTH_DAYS = 29.530589          # día lunar: de un amanecer al siguiente
ANOMALISTIC_MONTH_DAYS = 27.554550      # libración en longitud (azimut de la Tierra)
DRACONIC_MONTH_DAYS = 27.212221         # libración en latitud (elevación de la Tierra)
ECLIPSE_YEAR_DAYS = 346.62              # inclinación del eje (~1,54°): estacionalidad del Sol en los polos
KNOWN_PERIODS_DAYS = {"sinodico": SYNODIC_MONTH_DAYS, "anomalistico": ANOMALISTIC_MONTH_DAYS,
                      "draconico": DRACONIC_MONTH_DAYS, "anio_de_eclipses": ECLIPSE_YEAR_DAYS}
LUNAR_DAY_DAYS = SYNODIC_MONTH_DAYS
FFT_PAD_FACTOR = 16                      # relleno de ceros para interpolar los picos
FFT_TOP_PEAKS = 5
FFT_MIN_PERIOD_DAYS = 2.0
FFT_SYNODIC_BAND_DAYS = (24.0, 36.0)     # banda donde se busca el pico del mes sinódico
FFT_MATCH_RESOLUTION_FRACTION = 0.5      # un pico "coincide" con un periodo conocido si dista < ½ P²/T
HARMONIC_RIDGE = 1e-8                    # regularización relativa de los mínimos cuadrados
SUN_SYNODIC_HARMONICS = 8                # k·f_sinódico, k = 1..8 (la elevación no es una senoide)
SUN_SIDEBAND_HARMONICS = 2               # k·f_sinódico ± f_anomalístico para k = 1..2 (incluye el batido de ~411 d)
SUN_ANOMALISTIC_HARMONICS = 2
REFINE_STRIDE_SAMPLES = 6                # el afinado del periodo usa una de cada 6 muestras (velocidad)
PROJECTION_MIN_WINDOW_DAYS = 2 * SYNODIC_MONTH_DAYS
REFINE_GRID_POINTS = 41
REFINE_ITERATIONS = 4
REFINE_HALF_BAND_DAYS = 0.6
CV_HOLDOUT_FRACTION = 1 / 3              # validación cruzada: se prueba en el último tercio de los datos
MONTH_DAYS = 30.4375                     # mes medio, para agrupar el error por meses de horizonte
PROJECTION_MAX_DAYS = 366                # hasta dónde se proyecta tras el último dato
PROJECTION_MONTHS_AHEAD = 12
EVENT_MATCH_MAX_HOURS = 24.0             # una puesta proyectada solo empareja con una real a menos de 24 h
ERRORS_FILENAME = "proyeccion_errores.json"
ERRORS_VERSION = 1
ANALYSIS_DIR = Path(__file__).resolve().parents[1] / "analysis"
ERRORS_PATH = ANALYSIS_DIR / ERRORS_FILENAME      # viaja con el código (no con los datos): llega a producción en la imagen
CACHE_KIND_PROJECTION = "proyeccion"              # claves de la caché por mtime de services/lectura
CACHE_KIND_ERRORS = "errores"
KEY_SITES = "sitios"
KEY_VERSION = "version"
KEY_MAE_DEG = "mae_deg"
KEY_RMSE_DEG = "rmse_deg"
KEY_RMSE_MONTHS = "rmse_por_mes_deg"
KEY_RMSE_NAIVE = "rmse_referencia_ingenua_deg"
KEY_HASTA_UTC = "hasta_utc"
KEY_ERRORS = "errores"
KEY_SYNODIC_DAYS = "periodo_sinodico_dias"
KEY_WINDOW_DAYS = "ventana_dias"
KEY_EVENTS_SUN = "puestas_sol"
KEY_EVENTS_EARTH = "puestas_tierra"
KEY_EVENTS_REAL = "eventos_reales"
KEY_EVENTS_PROJECTED = "eventos_proyectados"
KEY_EVENTS_MATCHED_PCT = "emparejados_pct"
KEY_EVENTS_MAE_H = "mae_h"
KEY_EVENTS_MEDIAN_H = "mediana_h"
KEY_EVENTS_MAX_H = "max_h"
KEY_TRAFFIC_HITS = "semaforo"
KEY_MONTHS = "meses"
KEY_MONTHS_DETAIL = "meses_detalle"
KEY_MONTH = "mes"
KEY_HIT = "acierta"
KEY_HITS_PREFIX = "aciertos_"
KEY_REAL = "real"
MONTH_FORMAT = "%Y-%m"
KEY_PCT_SUN_REAL, KEY_PCT_SUN_PROJ = "pct_sol_real", "pct_sol_proy"
KEY_PCT_EARTH_REAL, KEY_PCT_EARTH_PROJ = "pct_tierra_real", "pct_tierra_proy"
KEY_PROJECTION_SIDE = "proyeccion"
# columnas de los LazyFrame del pipeline (app/analysis/pipeline.py)
COL_WINDOW_VISIBLE, COL_WINDOW_START, COL_WINDOW_END = "visible", "inicio", "fin"
COL_WINDOW_HOURS, COL_WINDOW_COMPLETE = "duracion_h", "completa"
COL_EVENT_KIND, EVENT_SET, EVENT_RISE = "tipo", "puesta", "salida"
COL_LUNAR_DAY, COL_SAMPLES, COL_RUN, COL_LAST_SAMPLE = "dia_lunar", "muestras", "tramo", "ultima"
COL_STATE_NOW, COL_STATE_BEFORE, COL_DATE = "ahora", "antes", "fecha"
COL_DAY_SUN_MAX, COL_DAY_SUN_MIN, COL_DAY_SUN_MEAN = "sol_el_max", "sol_el_min", "sol_el_media"
COL_LIB_AZ_RANGE, COL_LIB_AZ_STD = "tierra_az_rango_deg", "tierra_az_std_deg"
COL_LIB_EL_RANGE, COL_LIB_EL_STD, COL_LIB_EL_MEAN = "tierra_el_rango_deg", "tierra_el_std_deg", "tierra_el_media_deg"
SIM_DEFAULT_DAYS = 30
SIM_MIN_STEP_HOURS = 0.25
SIM_MAX_STEP_HOURS = 24.0
SIM_DEFAULT_STEP_HOURS = 1.0
SIM_MAX_POINTS = 9000                    # un año a paso de 1 h son 8785 puntos (≈ 75 ms, 2 MiB)
SIM_AZIMUTH_MAX_SUN_EL_DEG = 85.0        # con el Sol casi en el cénit el azimut gira de golpe y no se proyecta bien
ORIGEN_DATOS = "datos"
ORIGEN_PROYECCION = "proyeccion"
ROUTE_SIMULACION = "/simulacion/{site_id}"
ROUTER_TAG_SIMULACION = "simulacion"
PARAM_PASO_HORAS = "paso_horas"
KEY_PUNTOS = "puntos"
KEY_FIN_DATOS = "fin_datos_utc"
KEY_INCERTIDUMBRE = "incertidumbre_deg"
AVISO_PROYECCION = "proyeccion_estadistica"
AVISO_AZIMUT_CENIT = "azimut_solar_cenit"
MSG_SIM_BEFORE_DATA = "'desde' es anterior al primer dato disponible"
MSG_SIM_BEYOND_PROJECTION = "'hasta' supera el máximo proyectable ({days} días tras el último dato)"
MSG_SIM_STEP = "'paso_horas' debe estar entre {min:g} y {max:g}"
MSG_SIM_TOO_MANY_POINTS = "El rango pedido da {points} puntos; el máximo es {max} (sube paso_horas o acorta el rango)"
MSG_SIM_NOT_ENOUGH_DATA = "No hay datos suficientes para proyectar (se necesitan al menos {days:.0f} días)"
MSG_SIM_WARNING = ("Los puntos con origen «proyeccion» son una estimación estadística, no efemérides físicas: "
                   "sirven para explorar tendencias y animar el cielo. La incertidumbre sale de la validación contra Horizons.")
MSG_SIM_AZIMUTH_WARNING = "Con el Sol cerca del cénit el azimut solar proyectado no es fiable."
AVISO_SIN_INCERTIDUMBRE = "sin_incertidumbre"
MSG_SIM_NO_UNCERTAINTY = "No hay validación de este sitio: los puntos proyectados van sin incertidumbre_deg"
MSG_SIM_ERRORS_MISSING = "Falta la tabla de errores de la proyección (app/analysis/proyeccion_errores.json)"
MSG_SIM_ERRORS_INVALID = "La tabla de errores de la proyección no es válida; hay que regenerarla con analizar_proyeccion.py"


# ---------------------------------------------------------------- Horizons en vivo: coordenadas propias (app/services/horizons.py)
ROUTE_HORIZONS = "/horizons"
ROUTER_TAG_HORIZONS = "horizons"
PARAM_LAT = "lat"
PARAM_LON = "lon"
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
HORIZONS_SOURCE = "NASA/JPL Horizons"
HORIZONS_USER_AGENT = "ApogeoNavigator/1.0 (NASA Space Apps 2026; proyecto abierto)"   # genérico: sin datos personales
HORIZONS_TIMEOUT_S = 20.0
HORIZONS_CONNECT_TIMEOUT_S = 5.0
HORIZONS_RETRIES = 1                      # reintentos tras el primer intento (timeouts, caídas de red y 5xx de JPL)
HORIZONS_RETRY_BACKOFF_S = 0.5
HORIZONS_RETRY_STATUS = (500, 502, 503, 504)
HORIZONS_BODY_SUN = "10"
HORIZONS_BODY_EARTH = "399"
HORIZONS_CENTER = "coord@301"             # observador en la superficie de la Luna (301)
HORIZONS_COORD_TYPE = "GEODETIC"
HORIZONS_QUANTITIES = "4"                 # azimut y elevación aparentes
HORIZONS_SOE = "$$SOE"
HORIZONS_EOE = "$$EOE"
HORIZONS_HEADER_MOON = "Center body name: Moon (301)"
HORIZONS_HEADER_AIRLESS = "Atmos refraction: NO (AIRLESS)"
HORIZONS_TIME_FORMAT = "%Y-%m-%d %H:%M"
HORIZONS_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                   "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
HORIZONS_STEP_MIN_H = 1                   # paso mínimo: 1 h (la API no es para alta resolución)
HORIZONS_STEP_MAX_H = 24
HORIZONS_STEP_DEFAULT_H = 1
HORIZONS_MIN_YEAR = 1900
HORIZONS_MAX_YEAR = 2100
HORIZONS_COORD_DECIMALS = 4               # ≈ 3 m en la Luna: dos consultas casi iguales comparten caché
HORIZONS_CACHE_TTL_S = 86_400             # las efemérides no cambian: 24 h
HORIZONS_CACHE_MAX_ENTRIES = 64
HORIZONS_MAX_CONCURRENT = 2               # consultas simultáneas a JPL
HORIZONS_RATE_GLOBAL = (30, 60.0)         # consultas en vivo por ventana (segundos), entre todos los clientes
HORIZONS_RATE_CLIENT = (6, 60.0)          # ídem por cliente (según X-Forwarded-For o el socket)
HORIZONS_RETRY_AFTER_HEADER = "Retry-After"
HORIZONS_LOGGER = "apogeo.horizons"
HORIZONS_LOG_SNIPPET_CHARS = 160           # cuánto de una respuesta rara de JPL va al log (nunca al cliente)
CUSTOM_SITE_ID = "personalizado"
HTTP_TOO_MANY_REQUESTS = 429
HTTP_BAD_GATEWAY = 502
HTTP_GATEWAY_TIMEOUT = 504
KEY_FUENTE = "fuente"
KEY_EN_CACHE = "en_cache"
KEY_CONSULTA_UTC = "consultado_utc"
KEY_METRICAS = "metricas"
KEY_PUESTAS = "puestas"
ORIGEN_HORIZONS = "horizons_en_vivo"
DESC_TOO_MANY = "Demasiadas consultas en vivo a JPL Horizons"
DESC_BAD_GATEWAY = "JPL Horizons falló o devolvió algo inesperado"
DESC_GATEWAY_TIMEOUT = "JPL Horizons tardó demasiado en responder"
MSG_HORIZONS_YEARS = "Las fechas deben estar entre los años {min} y {max}"
MSG_HORIZONS_TOO_MANY = "Hay demasiadas consultas en vivo a JPL Horizons; espera {seconds} s y reintenta"
MSG_HORIZONS_TIMEOUT = "JPL Horizons tardó demasiado en responder; reduce el rango de fechas o reintenta en unos minutos"
MSG_HORIZONS_FAILED = "JPL Horizons no está disponible ahora; reintenta en unos minutos"
MSG_HORIZONS_UNEXPECTED = "JPL Horizons devolvió una respuesta que no se pudo interpretar; reintenta en unos minutos"
MSG_HORIZONS_NOT_MOON = "JPL Horizons no calculó la posición desde la superficie de la Luna sin refracción; no se devuelven datos"
MSG_HORIZONS_LOG_FAILED = "JPL Horizons: fallo %s en la consulta de %s"
MSG_HORIZONS_BAD_ROW_LOG = "fila de efemérides ilegible"
MSG_HORIZONS_LOG_REJECTED = "JPL Horizons rechazó la consulta de %s: %s"

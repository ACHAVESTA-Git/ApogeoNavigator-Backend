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
SEMAFORO_VERDE_MIN = 70.0          # provisional hasta que el PO confirme los umbrales
SEMAFORO_AMARILLO_MIN = 40.0
SEMAFORO_VERDE, SEMAFORO_AMARILLO, SEMAFORO_ROJO = "verde", "amarillo", "rojo"
METRICS_DEG_TOL = 0.001
MSG_SITE_NOT_IN_CATALOG = "El sitio no existe en sites.csv"
MSG_NO_EPHEMERIS = "El sitio no tiene efemérides cargadas"
MSG_EPHEM_INVALID = "El archivo de efemérides no cumple el estándar de CSV"
MSG_RANGE_ORDER = "'hasta' debe ser posterior a 'desde'"
MSG_QUERY_RANGE = "El rango máximo es de {max} días (se pidieron {days:.1f})"
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

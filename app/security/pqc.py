"""Firmas post-cuánticas ML-DSA-65 (FIPS 204) para integridad y no repudio.

Interfaz pública (acordada con el resto del backend; no cambiarla):

    firmar(datos: bytes) -> bytes
    verificar(datos: bytes, firma: bytes) -> bool
    ALGORITMO = "ML-DSA-65"
    clave_publica() -> bytes

``firmar`` y ``verificar`` aceptan además ``contexto`` (solo por nombre, opcional) para
separar dominios: una firma hecha con ``contexto=b"auditoria"`` no valida como firma de
``contexto=b"carga-csv"`` aunque los datos coincidan.

Claves
------
- Producción: ``PQC_PRIVATE_KEY`` (semilla de 32 bytes en base64) y ``PQC_PUBLIC_KEY``
  (1952 bytes en base64), desde secretos de Cloudflare o GCP. Se generan con
  ``scripts/pqc/generar_claves.py``. Nunca van al repositorio.
- Con solo ``PQC_PUBLIC_KEY`` el módulo puede verificar pero no firmar.
- Desarrollo: si no hay claves, se genera un par en ``.pqc_dev/`` (carpeta con su propio
  ``.gitignore``) y se emite un ``RuntimeWarning``. Con ``K_SERVICE`` (Cloud Run) o
  ``ENVIRONMENT=production`` esto está prohibido y se lanza ``ErrorClavesPQC``.

Librería: ``cryptography`` (PyCA) >= 47. La clave privada se guarda como semilla (FIPS 204,
``ξ``), de la que se deriva el par completo.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
import threading
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey, MLDSA65PublicKey

# ---------------------------------------------------------------- constantes
ALGORITMO = "ML-DSA-65"
ESTANDAR = "FIPS 204"
ENV_PRIVATE_KEY = "PQC_PRIVATE_KEY"
ENV_PUBLIC_KEY = "PQC_PUBLIC_KEY"
ENV_DEV_KEY_DIR = "PQC_DEV_KEY_DIR"
ENV_CLOUD_RUN_MARKER = "K_SERVICE"
ENV_ENVIRONMENT = "ENVIRONMENT"
ENVIRONMENT_PRODUCTION = "production"

SEED_BYTES = 32
PUBLIC_KEY_BYTES = 1952
SIGNATURE_BYTES = 3309
CONTEXT_MAX_BYTES = 255
CONTEXT_DEFAULT = b""

DEV_KEY_DIRNAME = ".pqc_dev"
DEV_KEY_FILENAME = "dev_keys.json"
DEV_GITIGNORE_NAME = ".gitignore"
DEV_GITIGNORE_CONTENT = "*\n"
DEV_DIR_MODE = 0o700
DEV_FILE_MODE = 0o600
DEV_TMP_SUFFIX = ".tmp"
TEXT_ENCODING = "utf-8"
DEFAULT_DEV_KEY_DIR = Path(__file__).resolve().parents[2] / DEV_KEY_DIRNAME

MANIFEST_CONTEXT = b"manifiesto-datos"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = 1
MANIFEST_SUFFIX = ".csv"
MANIFEST_GLOB = f"*{MANIFEST_SUFFIX}"
MANIFEST_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MANIFEST_FILE_MODE = 0o644
MANIFEST_SIGNATURE_FIELD = "firma_b64"
MANIFEST_TMP_SUFFIX = ".tmp"
MANIFEST_HASH_ALGORITHM = "sha3_256"
JSON_SEPARATORS = (",", ":")

MSG_MANIFEST_VERSION = "Versión de manifiesto no soportada: {version}."
MSG_MANIFEST_ALGORITHM = f"El algoritmo del manifiesto no es {ALGORITMO}."
MSG_MANIFEST_NO_SIGNATURE = "El manifiesto no trae firma."
MSG_MANIFEST_BAD_SIGNATURE = "La firma del manifiesto NO es válida para la clave pública dada."
MSG_MANIFEST_FINGERPRINT = "La huella de la clave pública del manifiesto no coincide con la clave usada."
MSG_MANIFEST_MALFORMED = "El manifiesto está mal formado: {detalle}."
MSG_MANIFEST_MISSING_FILE = "{nombre}: figura en el manifiesto pero no existe."
MSG_MANIFEST_SIZE = "{nombre}: el tamaño no coincide (manifiesto {esperado}, real {real})."
MSG_MANIFEST_HASH = "{nombre}: el SHA3-256 no coincide; el archivo fue modificado."
MSG_MANIFEST_UNLISTED = "{nombre}: existe en la carpeta pero no figura en el manifiesto."
MSG_MANIFEST_BAD_NAME = "Nombre de archivo no permitido en el manifiesto: {nombre!r}."

MSG_NO_PRIVATE = (
    f"No hay clave privada ({ENV_PRIVATE_KEY}); este proceso solo puede verificar firmas."
)
MSG_PRODUCTION_NO_KEYS = (
    f"Faltan {ENV_PRIVATE_KEY} y {ENV_PUBLIC_KEY} y el entorno es de producción: "
    "no se generan claves automáticamente. Créalas con scripts/pqc/generar_claves.py "
    "y guárdalas en el gestor de secretos."
)
MSG_DEV_KEYS_WARNING = (
    f"{ALGORITMO}: sin {ENV_PRIVATE_KEY}; se usa un par de claves de DESARROLLO en {{ruta}}. "
    "Estas firmas no son válidas para producción."
)
MSG_BAD_BASE64 = "{nombre} no es base64 válido."
MSG_BAD_LENGTH = "{nombre} debe tener {esperado} bytes y tiene {actual}."
MSG_KEY_MISMATCH = f"{ENV_PUBLIC_KEY} no corresponde a {ENV_PRIVATE_KEY}."
MSG_BAD_DEV_FILE = "El archivo de claves de desarrollo {ruta} es inválido; bórralo para regenerarlo."
MSG_NOT_BYTES = "{nombre} debe ser bytes, no {tipo}."
MSG_BAD_PUBLIC_KEY = "Clave pública inválida: se esperaban {esperado} bytes."
MSG_CONTEXT_TOO_LONG = f"El contexto admite como máximo {CONTEXT_MAX_BYTES} bytes."


class ErrorClavesPQC(RuntimeError):
    """Las claves no existen, son inválidas o no se pueden usar en este entorno."""


@dataclass(frozen=True)
class _Claves:
    privada: MLDSA65PrivateKey | None
    publica: MLDSA65PublicKey
    publica_bytes: bytes


_lock = threading.Lock()
_claves: _Claves | None = None


# ---------------------------------------------------------------- utilidades internas
def _como_bytes(valor, nombre: str) -> bytes:
    if not isinstance(valor, (bytes, bytearray, memoryview)):
        raise TypeError(MSG_NOT_BYTES.format(nombre=nombre, tipo=type(valor).__name__))
    return bytes(valor)


def _validar_contexto(contexto) -> bytes:
    contexto = _como_bytes(contexto, "contexto")
    if len(contexto) > CONTEXT_MAX_BYTES:
        raise ValueError(MSG_CONTEXT_TOO_LONG)
    return contexto


def _decodificar(valor: str, nombre: str, esperado: int) -> bytes:
    try:
        raw = base64.b64decode(valor.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ErrorClavesPQC(MSG_BAD_BASE64.format(nombre=nombre)) from None
    if len(raw) != esperado:
        raise ErrorClavesPQC(MSG_BAD_LENGTH.format(nombre=nombre, esperado=esperado, actual=len(raw)))
    return raw


def _codificar(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _desde_semilla(semilla: bytes) -> _Claves:
    privada = MLDSA65PrivateKey.from_seed_bytes(semilla)
    publica = privada.public_key()
    return _Claves(privada, publica, publica.public_bytes_raw())


def _es_produccion() -> bool:
    return bool(os.getenv(ENV_CLOUD_RUN_MARKER)) or (
        os.getenv(ENV_ENVIRONMENT, "").strip().lower() == ENVIRONMENT_PRODUCTION
    )


def _dev_dir() -> Path:
    return Path(os.getenv(ENV_DEV_KEY_DIR) or DEFAULT_DEV_KEY_DIR)


def _escribir_atomico(ruta: Path, contenido: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=ruta.name, suffix=DEV_TMP_SUFFIX)
    try:
        with os.fdopen(fd, "w", encoding=TEXT_ENCODING) as f:
            f.write(contenido)
        os.chmod(tmp, DEV_FILE_MODE)
        os.replace(tmp, ruta)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _claves_de_desarrollo() -> _Claves:
    carpeta = _dev_dir()
    archivo = carpeta / DEV_KEY_FILENAME
    if archivo.is_file():
        try:
            datos = json.loads(archivo.read_text(encoding=TEXT_ENCODING))
            claves = _desde_semilla(_decodificar(datos[ENV_PRIVATE_KEY], ENV_PRIVATE_KEY, SEED_BYTES))
            if claves.publica_bytes != _decodificar(datos[ENV_PUBLIC_KEY], ENV_PUBLIC_KEY, PUBLIC_KEY_BYTES):
                raise ErrorClavesPQC(MSG_KEY_MISMATCH)
        except (OSError, ValueError, KeyError, TypeError, ErrorClavesPQC):
            raise ErrorClavesPQC(MSG_BAD_DEV_FILE.format(ruta=archivo)) from None
    else:
        carpeta.mkdir(parents=True, exist_ok=True)
        os.chmod(carpeta, DEV_DIR_MODE)
        (carpeta / DEV_GITIGNORE_NAME).write_text(DEV_GITIGNORE_CONTENT, encoding=TEXT_ENCODING)
        semilla, _ = generar_par_raw()
        claves = _desde_semilla(semilla)
        _escribir_atomico(archivo, json.dumps({
            "algoritmo": ALGORITMO,
            ENV_PRIVATE_KEY: _codificar(semilla),
            ENV_PUBLIC_KEY: _codificar(claves.publica_bytes),
        }, indent=2))
    warnings.warn(MSG_DEV_KEYS_WARNING.format(ruta=archivo), RuntimeWarning, stacklevel=4)
    return claves


def _cargar() -> _Claves:
    privada_b64 = os.getenv(ENV_PRIVATE_KEY, "").strip()
    publica_b64 = os.getenv(ENV_PUBLIC_KEY, "").strip()
    if privada_b64:
        claves = _desde_semilla(_decodificar(privada_b64, ENV_PRIVATE_KEY, SEED_BYTES))
        if publica_b64 and _decodificar(publica_b64, ENV_PUBLIC_KEY, PUBLIC_KEY_BYTES) != claves.publica_bytes:
            raise ErrorClavesPQC(MSG_KEY_MISMATCH)
        return claves
    if publica_b64:
        publica_bytes = _decodificar(publica_b64, ENV_PUBLIC_KEY, PUBLIC_KEY_BYTES)
        return _Claves(None, MLDSA65PublicKey.from_public_bytes(publica_bytes), publica_bytes)
    if _es_produccion():
        raise ErrorClavesPQC(MSG_PRODUCTION_NO_KEYS)
    return _claves_de_desarrollo()


def _obtener() -> _Claves:
    global _claves
    if _claves is None:
        with _lock:
            if _claves is None:
                _claves = _cargar()
    return _claves


# ---------------------------------------------------------------- API pública
def generar_par_raw() -> tuple[bytes, bytes]:
    """Devuelve (semilla de 32 bytes, clave pública de 1952 bytes) de un par nuevo."""
    privada = MLDSA65PrivateKey.generate()
    return privada.private_bytes_raw(), privada.public_key().public_bytes_raw()


def generar_par() -> tuple[str, str]:
    """Devuelve (PQC_PRIVATE_KEY, PQC_PUBLIC_KEY) en base64 para guardarlas como secretos."""
    semilla, publica = generar_par_raw()
    return _codificar(semilla), _codificar(publica)


def clave_publica_desde_semilla(semilla_b64: str) -> bytes:
    """Deriva la clave pública (1952 bytes) de una PQC_PRIVATE_KEY en base64."""
    return _desde_semilla(_decodificar(semilla_b64, ENV_PRIVATE_KEY, SEED_BYTES)).publica_bytes


def firmar(datos: bytes, *, contexto: bytes = CONTEXT_DEFAULT) -> bytes:
    """Firma ``datos`` con ML-DSA-65 y devuelve la firma (3309 bytes)."""
    claves = _obtener()
    if claves.privada is None:
        raise ErrorClavesPQC(MSG_NO_PRIVATE)
    return claves.privada.sign(_como_bytes(datos, "datos"), _validar_contexto(contexto))


def verificar(datos: bytes, firma: bytes, *, contexto: bytes = CONTEXT_DEFAULT) -> bool:
    """True si ``firma`` es válida para ``datos`` con la clave pública configurada."""
    return verificar_con_clave(_obtener().publica_bytes, datos, firma, contexto=contexto)


def verificar_con_clave(clave_publica_bytes: bytes, datos: bytes, firma: bytes, *,
                        contexto: bytes = CONTEXT_DEFAULT) -> bool:
    """Verifica con una clave pública explícita (p. ej. la publicada en /api/seguridad)."""
    clave_publica_bytes = _como_bytes(clave_publica_bytes, "clave_publica")
    if len(clave_publica_bytes) != PUBLIC_KEY_BYTES:
        raise ValueError(MSG_BAD_PUBLIC_KEY.format(esperado=PUBLIC_KEY_BYTES))
    datos = _como_bytes(datos, "datos")
    firma = _como_bytes(firma, "firma")
    contexto = _validar_contexto(contexto)
    try:
        MLDSA65PublicKey.from_public_bytes(clave_publica_bytes).verify(firma, datos, contexto)
    except InvalidSignature:
        return False
    return True


def clave_publica() -> bytes:
    """Clave pública cruda (1952 bytes) del par en uso."""
    return _obtener().publica_bytes


def huella_clave_publica(clave: bytes | None = None) -> str:
    """Huella SHA3-256 (hex) de la clave pública, para identificarla en manifiestos y registros."""
    return hashlib.sha3_256(clave if clave is not None else clave_publica()).hexdigest()


def reiniciar() -> None:
    """Olvida las claves cargadas (para pruebas y para rotar claves sin reiniciar el proceso)."""
    global _claves
    with _lock:
        _claves = None


# ---------------------------------------------------------------- manifiesto firmado de data/
def canonicalizar(objeto) -> bytes:
    """JSON canónico (claves ordenadas, sin espacios, UTF-8): los bytes que se firman."""
    return json.dumps(objeto, sort_keys=True, separators=JSON_SEPARATORS,
                      ensure_ascii=False).encode(TEXT_ENCODING)


def _hash_hex(contenido: bytes) -> str:
    return hashlib.new(MANIFEST_HASH_ALGORITHM, contenido).hexdigest()


def _entrada(nombre: str, contenido: bytes) -> dict:
    return {"nombre": nombre, "bytes": len(contenido), MANIFEST_HASH_ALGORITHM: _hash_hex(contenido)}


def _csv_de(carpeta: Path) -> list[Path]:
    return sorted(p for p in Path(carpeta).glob(MANIFEST_GLOB) if p.is_file() and not p.is_symlink())


def generar_manifiesto(carpeta: Path, *, contenidos_nuevos: dict[str, bytes] | None = None,
                       ahora: datetime | None = None) -> dict:
    """Manifiesto firmado de los CSV de ``carpeta`` (sin recursión; ``_historial`` no entra).

    ``contenidos_nuevos`` permite incluir un archivo que todavía no está en disco o que se
    va a reemplazar (nombre -> bytes), para firmar antes del ``os.replace``.
    """
    contenidos = {p.name: p.read_bytes() for p in _csv_de(carpeta)}
    for nombre, contenido in (contenidos_nuevos or {}).items():
        if Path(nombre).name != nombre or not nombre.endswith(MANIFEST_SUFFIX):
            raise ValueError(MSG_MANIFEST_BAD_NAME.format(nombre=nombre))
        contenidos[nombre] = _como_bytes(contenido, nombre)
    sello = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    manifiesto = {
        "version": MANIFEST_VERSION,
        "algoritmo": ALGORITMO,
        "generado_utc": sello.strftime(MANIFEST_TIME_FORMAT),
        "huella_clave_publica": huella_clave_publica(),
        "archivos": [_entrada(n, contenidos[n]) for n in sorted(contenidos)],
    }
    firma = firmar(canonicalizar(manifiesto), contexto=MANIFEST_CONTEXT)
    manifiesto[MANIFEST_SIGNATURE_FIELD] = _codificar(firma)
    return manifiesto


def escribir_manifiesto(carpeta: Path, manifiesto: dict) -> Path:
    """Escribe ``manifest.json`` de forma atómica (temporal + os.replace)."""
    destino = Path(carpeta) / MANIFEST_FILENAME
    fd, tmp = tempfile.mkstemp(dir=carpeta, prefix=MANIFEST_FILENAME, suffix=MANIFEST_TMP_SUFFIX)
    try:
        with os.fdopen(fd, "w", encoding=TEXT_ENCODING) as f:
            json.dump(manifiesto, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, MANIFEST_FILE_MODE)
        os.replace(tmp, destino)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return destino


def verificar_manifiesto(manifiesto: dict, carpeta: Path | None = None,
                         clave_publica_bytes: bytes | None = None) -> list[str]:
    """Devuelve la lista de problemas encontrados; lista vacía = todo correcto.

    Comprueba versión, algoritmo, huella y firma (con ``clave_publica_bytes`` o la del
    proceso) y, si se da ``carpeta``, tamaño y SHA3-256 de cada CSV y que no sobren CSV.
    """
    problemas: list[str] = []
    try:
        if manifiesto["version"] != MANIFEST_VERSION:
            return [MSG_MANIFEST_VERSION.format(version=manifiesto["version"])]
        if manifiesto["algoritmo"] != ALGORITMO:
            return [MSG_MANIFEST_ALGORITHM]
        firma_b64 = manifiesto.get(MANIFEST_SIGNATURE_FIELD)
        if not firma_b64:
            return [MSG_MANIFEST_NO_SIGNATURE]
        firma = base64.b64decode(firma_b64, validate=True)
        sin_firma = {k: v for k, v in manifiesto.items() if k != MANIFEST_SIGNATURE_FIELD}
        clave = clave_publica_bytes if clave_publica_bytes is not None else clave_publica()
        if manifiesto["huella_clave_publica"] != huella_clave_publica(clave):
            problemas.append(MSG_MANIFEST_FINGERPRINT)
        if not verificar_con_clave(clave, canonicalizar(sin_firma), firma, contexto=MANIFEST_CONTEXT):
            problemas.append(MSG_MANIFEST_BAD_SIGNATURE)
        entradas = {e["nombre"]: e for e in manifiesto["archivos"]}
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        return [MSG_MANIFEST_MALFORMED.format(detalle=type(exc).__name__)]
    if carpeta is not None:
        carpeta = Path(carpeta)
        for nombre, entrada in sorted(entradas.items()):
            ruta = carpeta / nombre
            if Path(nombre).name != nombre or not ruta.is_file():
                problemas.append(MSG_MANIFEST_MISSING_FILE.format(nombre=nombre))
                continue
            contenido = ruta.read_bytes()
            if len(contenido) != entrada.get("bytes"):
                problemas.append(MSG_MANIFEST_SIZE.format(nombre=nombre, esperado=entrada.get("bytes"),
                                                          real=len(contenido)))
            if _hash_hex(contenido) != entrada.get(MANIFEST_HASH_ALGORITHM):
                problemas.append(MSG_MANIFEST_HASH.format(nombre=nombre))
        problemas += [MSG_MANIFEST_UNLISTED.format(nombre=p.name) for p in _csv_de(carpeta)
                      if p.name not in entradas]
    return problemas

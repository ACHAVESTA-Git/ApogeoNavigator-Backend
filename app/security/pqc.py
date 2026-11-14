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

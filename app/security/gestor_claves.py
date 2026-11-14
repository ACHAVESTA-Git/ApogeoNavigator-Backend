"""Gestor de claves post-cuánticas: generación de pares por usuario y cifrado KEM-DEM.

Política «revocar y reemitir»:
- Por usuario hay dos pares: ML-KEM-1024 (FIPS 203, recibir datos cifrados) y ML-DSA-65 (FIPS 204, firmar).
- La clave PRIVADA se entrega una sola vez a quien la pide y el servidor NO la guarda. El admin nunca la ve.
- Si se pierde, se revoca el par y se emite uno nuevo. Lo firmado antes sigue siendo verificable con la
  pública revocada (las públicas nunca se borran).

Límite honesto: Python no permite borrar con garantías las copias en memoria de un ``bytes``. Se borra lo que
se puede (``bytearray``) y la protección real es no persistir, no registrar y no cachear la privada.

KEM-DEM (cifrado de datos personales en reposo): ML-KEM-1024 + HKDF-SHA256 + AES-256-GCM.
Formato del blob: versión (1 byte) | id de la clave KEM (8) | texto cifrado del KEM (1568) | nonce (12) | AES-GCM (datos + etiqueta de 16).
El id permite rotar la clave del servidor: se cifra siempre con la actual y se descifra con la que indique el id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey, MLDSA65PublicKey
from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM1024PrivateKey, MLKEM1024PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core import config as app_config
from app.security import pqc

# ---------------------------------------------------------------- constantes
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
DEV_FILE_MODE = 0o600
DEV_DIR_MODE = 0o700
EMAIL_HASH_KEY_BYTES = 32
TEXT_ENCODING = "utf-8"

MSG_TIPO_DESCONOCIDO = "Tipo de clave desconocido: {tipo!r}."
MSG_NO_SERVER_KEYS = (
    f"Faltan {ENV_KEM_PRIVATE} o {ENV_EMAIL_HASH_KEY} y el entorno es de producción: "
    "no se generan claves del servidor automáticamente."
)
MSG_SERVER_KEYS_DEV = "Claves del servidor (KEM y hash de email) de DESARROLLO en {ruta}; no valen para producción."
MSG_BAD_BLOB = "Dato cifrado inválido o manipulado."
MSG_UNKNOWN_KEY = "El dato está cifrado con una clave del servidor que ya no está configurada."
MSG_BAD_KEY = "{nombre} inválida: {detalle}."


class ErrorGestorClaves(RuntimeError):
    """Claves del servidor ausentes o inválidas, o dato cifrado manipulado."""


# ---------------------------------------------------------------- pares por usuario
@dataclass
class ParNuevo:
    """Par recién generado. ``privada_pem`` es un bytearray para poder borrarlo después de entregarlo."""
    tipo: str
    algoritmo: str
    publica: bytes
    huella: str
    tamano_publica: int
    tamano_privada: int
    privada_pem: bytearray


def huella(publica: bytes) -> str:
    """SHA3-256 (hex) de la clave pública cruda; mismo criterio que ``pqc.huella_clave_publica``."""
    return hashlib.sha3_256(publica).hexdigest()


def generar_par(tipo: str) -> ParNuevo:
    """Genera un par del tipo dado. La privada sale en PEM (PKCS#8, forma de semilla) y no se almacena."""
    if tipo == TIPO_KEM:
        privada = MLKEM1024PrivateKey.generate()
    elif tipo == TIPO_FIRMA:
        privada = MLDSA65PrivateKey.generate()
    else:
        raise ValueError(MSG_TIPO_DESCONOCIDO.format(tipo=tipo))
    publica = privada.public_key().public_bytes_raw()
    pem = bytearray(privada.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return ParNuevo(tipo=tipo, algoritmo=ALGORITMO_POR_TIPO[tipo], publica=publica, huella=huella(publica),
                    tamano_publica=len(publica), tamano_privada=TAMANO_PRIVADA_ENTREGADA[tipo], privada_pem=pem)


def borrar(buffer: bytearray | None) -> None:
    """Pone a cero un bytearray (mejor esfuerzo: no alcanza a las copias que haya hecho el intérprete)."""
    if buffer:
        buffer[:] = bytes(len(buffer))


def publica_pem(tipo: str, publica: bytes) -> str:
    """Clave pública en PEM (SubjectPublicKeyInfo) a partir de los bytes crudos guardados."""
    if tipo == TIPO_KEM:
        clave = MLKEM1024PublicKey.from_public_bytes(publica)
    elif tipo == TIPO_FIRMA:
        clave = MLDSA65PublicKey.from_public_bytes(publica)
    else:
        raise ValueError(MSG_TIPO_DESCONOCIDO.format(tipo=tipo))
    return clave.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


# ---------------------------------------------------------------- KEM-DEM
def _derivar_clave(secreto: bytes, texto_kem: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=SHARED_SECRET_BYTES, salt=None,
                info=KEM_DEM_INFO + texto_kem[:32]).derive(secreto)


def id_de_clave(publica: bytes) -> bytes:
    """Identificador corto (8 bytes) de una clave KEM pública: va en cada blob para saber con qué clave descifrar."""
    return hashlib.sha3_256(publica).digest()[:KEY_ID_BYTES]


def kem_dem_cifrar(publica: bytes, datos: bytes, aad: bytes = b"") -> bytes:
    """Cifra ``datos`` para el dueño de la clave KEM ``publica`` (1568 bytes crudos)."""
    secreto, texto_kem = MLKEM1024PublicKey.from_public_bytes(publica).encapsulate()
    nonce = os.urandom(KEM_DEM_NONCE_BYTES)
    cabecera = KEM_DEM_VERSION + id_de_clave(publica) + texto_kem + nonce
    cifrado = AESGCM(_derivar_clave(secreto, texto_kem)).encrypt(nonce, datos, aad + cabecera[:1 + KEY_ID_BYTES])
    return cabecera + cifrado


def _partes(blob: bytes) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    """(versión+id, id, texto KEM, nonce, cifrado) o ErrorGestorClaves si el blob no tiene la forma esperada."""
    if len(blob) < KEM_DEM_HEADER_BYTES + KEM_DEM_TAG_BYTES or blob[:1] != KEM_DEM_VERSION:
        raise ErrorGestorClaves(MSG_BAD_BLOB)
    inicio_ct = 1 + KEY_ID_BYTES
    return (blob[:inicio_ct], blob[1:inicio_ct], blob[inicio_ct:inicio_ct + KEM_CIPHERTEXT_BYTES],
            blob[inicio_ct + KEM_CIPHERTEXT_BYTES:KEM_DEM_HEADER_BYTES], blob[KEM_DEM_HEADER_BYTES:])


def id_del_blob(blob: bytes) -> bytes:
    """Id de la clave KEM con la que se cifró el blob."""
    return _partes(blob)[1]


def kem_dem_descifrar(privada: MLKEM1024PrivateKey | bytes, blob: bytes, aad: bytes = b"") -> bytes:
    """Descifra un blob de ``kem_dem_cifrar``. ``privada`` es la clave o su semilla de 64 bytes."""
    if isinstance(privada, (bytes, bytearray)):
        privada = MLKEM1024PrivateKey.from_seed_bytes(bytes(privada))
    cabecera, kid, texto_kem, nonce, cifrado = _partes(blob)
    if kid != id_de_clave(privada.public_key().public_bytes_raw()):
        raise ErrorGestorClaves(MSG_BAD_BLOB)
    try:
        secreto = privada.decapsulate(texto_kem)
        return AESGCM(_derivar_clave(secreto, texto_kem)).decrypt(nonce, cifrado, aad + cabecera)
    except Exception:  # etiqueta GCM inválida, texto KEM inválido... todo es «manipulado»
        raise ErrorGestorClaves(MSG_BAD_BLOB) from None


# ---------------------------------------------------------------- claves del servidor (datos personales)
@dataclass(frozen=True)
class _ClavesServidor:
    """Llavero del servidor: la clave KEM ACTUAL cifra; todas (actual y anteriores) descifran por id."""
    kem: MLKEM1024PrivateKey
    kem_publica: bytes
    kem_por_id: dict
    claves_hash_email: tuple     # la primera es la actual; las demás, anteriores


_lock = threading.Lock()
_servidor: _ClavesServidor | None = None


def _b64(valor: str, nombre: str, esperado: int) -> bytes:
    try:
        raw = base64.b64decode(valor.strip(), validate=True)
    except ValueError:
        raise ErrorGestorClaves(MSG_BAD_KEY.format(nombre=nombre, detalle="no es base64")) from None
    if len(raw) != esperado:
        raise ErrorGestorClaves(MSG_BAD_KEY.format(nombre=nombre, detalle=f"debe tener {esperado} bytes"))
    return raw


def _dev_file() -> Path:
    return Path(os.getenv(pqc.ENV_DEV_KEY_DIR) or pqc.DEFAULT_DEV_KEY_DIR) / DEV_SERVER_KEYS_FILENAME


def _lista(valor: str, nombre: str, esperado: int) -> list[bytes]:
    return [_b64(item, nombre, esperado) for item in valor.split(LIST_SEPARATOR) if item.strip()]


def _servidor_desde(semilla: bytes, clave_hash: bytes, semillas_previas: list[bytes] | None = None,
                    hashes_previos: list[bytes] | None = None) -> _ClavesServidor:
    kem = MLKEM1024PrivateKey.from_seed_bytes(semilla)
    publica = kem.public_key().public_bytes_raw()
    por_id = {id_de_clave(publica): kem}
    for previa in semillas_previas or []:
        clave = MLKEM1024PrivateKey.from_seed_bytes(previa)
        por_id.setdefault(id_de_clave(clave.public_key().public_bytes_raw()), clave)
    return _ClavesServidor(kem, publica, por_id, (clave_hash, *(hashes_previos or [])))


def _cargar_servidor() -> _ClavesServidor:
    semilla_b64 = os.getenv(ENV_KEM_PRIVATE, "").strip()
    hash_b64 = os.getenv(ENV_EMAIL_HASH_KEY, "").strip()
    if semilla_b64 and hash_b64:
        return _servidor_desde(
            _b64(semilla_b64, ENV_KEM_PRIVATE, KEM_SEED_BYTES), _b64(hash_b64, ENV_EMAIL_HASH_KEY, EMAIL_HASH_KEY_BYTES),
            _lista(os.getenv(ENV_KEM_PREVIOUS, ""), ENV_KEM_PREVIOUS, KEM_SEED_BYTES),
            _lista(os.getenv(ENV_EMAIL_HASH_PREVIOUS, ""), ENV_EMAIL_HASH_PREVIOUS, EMAIL_HASH_KEY_BYTES))
    if app_config.is_production():
        raise ErrorGestorClaves(MSG_NO_SERVER_KEYS)
    archivo = _dev_file()
    if archivo.is_file():
        try:
            datos = json.loads(archivo.read_text(encoding=TEXT_ENCODING))
            claves = _servidor_desde(_b64(datos[ENV_KEM_PRIVATE], ENV_KEM_PRIVATE, KEM_SEED_BYTES),
                                     _b64(datos[ENV_EMAIL_HASH_KEY], ENV_EMAIL_HASH_KEY, EMAIL_HASH_KEY_BYTES))
        except (OSError, ValueError, KeyError, TypeError):
            raise ErrorGestorClaves(MSG_BAD_KEY.format(nombre=str(archivo), detalle="archivo de desarrollo corrupto")) from None
    else:
        archivo.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(archivo.parent, DEV_DIR_MODE)
        (archivo.parent / pqc.DEV_GITIGNORE_NAME).write_text(pqc.DEV_GITIGNORE_CONTENT, encoding=TEXT_ENCODING)
        semilla, clave_hash = os.urandom(KEM_SEED_BYTES), os.urandom(EMAIL_HASH_KEY_BYTES)
        claves = _servidor_desde(semilla, clave_hash)
        fd = os.open(archivo, os.O_WRONLY | os.O_CREAT | os.O_EXCL, DEV_FILE_MODE)
        with os.fdopen(fd, "w", encoding=TEXT_ENCODING) as f:
            json.dump({ENV_KEM_PRIVATE: base64.b64encode(semilla).decode("ascii"),
                       ENV_EMAIL_HASH_KEY: base64.b64encode(clave_hash).decode("ascii")}, f)
    warnings.warn(MSG_SERVER_KEYS_DEV.format(ruta=archivo), RuntimeWarning, stacklevel=3)
    return claves


def _obtener() -> _ClavesServidor:
    global _servidor
    if _servidor is None:
        with _lock:
            if _servidor is None:
                _servidor = _cargar_servidor()
    return _servidor


def reiniciar() -> None:
    """Olvida las claves del servidor (pruebas y rotación sin reiniciar)."""
    global _servidor
    with _lock:
        _servidor = None


def generar_claves_servidor() -> tuple[str, str]:
    """(PQC_KEM_PRIVATE_KEY, EMAIL_HASH_KEY) nuevas en base64, para guardarlas como secretos."""
    return (base64.b64encode(os.urandom(KEM_SEED_BYTES)).decode("ascii"),
            base64.b64encode(os.urandom(EMAIL_HASH_KEY_BYTES)).decode("ascii"))


def _normalizar_email(email: str) -> str:
    return email.strip().lower()


def cifrar_email(email: str) -> bytes:
    """Email cifrado en reposo (KEM-DEM con la clave KEM ACTUAL del servidor)."""
    return kem_dem_cifrar(_obtener().kem_publica, _normalizar_email(email).encode(TEXT_ENCODING), EMAIL_AAD)


def _clave_para(blob: bytes) -> MLKEM1024PrivateKey:
    clave = _obtener().kem_por_id.get(id_del_blob(blob))
    if clave is None:
        raise ErrorGestorClaves(MSG_UNKNOWN_KEY)
    return clave


def descifrar_email(blob: bytes) -> str:
    """Descifra con la clave que indique el blob (la actual o una anterior configurada)."""
    return kem_dem_descifrar(_clave_para(blob), blob, EMAIL_AAD).decode(TEXT_ENCODING)


def necesita_reencriptar(blob: bytes) -> bool:
    """True si el blob se cifró con una clave que ya no es la actual."""
    return id_del_blob(blob) != id_de_clave(_obtener().kem_publica)


def reencriptar_email(blob: bytes) -> bytes:
    """Descifra con la clave que corresponda y vuelve a cifrar con la actual (paso de una rotación de clave)."""
    return cifrar_email(descifrar_email(blob))


def email_hash(email: str) -> str:
    """HMAC-SHA256 (hex) del email normalizado con la clave ACTUAL, para búsquedas exactas sin descifrar."""
    return _hmac_email(_obtener().claves_hash_email[0], email)


def _hmac_email(clave: bytes, email: str) -> str:
    return hmac.new(clave, _normalizar_email(email).encode(TEXT_ENCODING), hashlib.sha256).hexdigest()


def email_hash_candidatos(email: str) -> list[str]:
    """Hashes con la clave actual y las anteriores: durante una rotación, buscar con cualquiera de ellos."""
    return [_hmac_email(clave, email) for clave in _obtener().claves_hash_email]

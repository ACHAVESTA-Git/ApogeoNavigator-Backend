"""Contraseñas del login local: Argon2id (argon2-cffi, parámetros por defecto de la librería = RFC 9106 perfil de bajo uso de memoria).

Nunca se guarda ni se registra la contraseña. Si el usuario no existe se verifica contra un hash de relleno para que
el tiempo de respuesta no revele si el email está registrado.
"""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core import config as c

_hasher = PasswordHasher()
_relleno: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _hash_de_relleno() -> str:
    global _relleno
    if _relleno is None:
        _relleno = _hasher.hash(c.AUTH_DUMMY_PASSWORD)
    return _relleno


def verify_password(password_hash: str | None, password: str) -> bool:
    """True solo si coincide. Sin hash (usuario inexistente o sin login local) gasta el mismo tiempo y devuelve False."""
    try:
        _hasher.verify(password_hash or _hash_de_relleno(), password)
    except (VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)

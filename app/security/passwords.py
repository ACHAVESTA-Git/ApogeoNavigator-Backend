"""Contraseñas del login local: Argon2id con el perfil OWASP (m=19 MiB, t=2, p=1).

Nunca se guarda ni se registra la contraseña. Si el usuario no existe se verifica contra un hash de relleno para que
el tiempo de respuesta no revele si el email está registrado. Las verificaciones simultáneas están acotadas por un
semáforo: cada una usa 19 MiB y, sin tope, unas decenas de logins a la vez agotarían la memoria de la instancia.
"""
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core import config as c

_hasher = PasswordHasher(time_cost=c.AUTH_ARGON2_TIME_COST, memory_cost=c.AUTH_ARGON2_MEMORY_KIB,
                         parallelism=c.AUTH_ARGON2_PARALLELISM)
_semaforo = threading.BoundedSemaphore(c.AUTH_HASH_CONCURRENCY)
_relleno: str | None = None


class VerificadorOcupado(Exception):
    """No hubo hueco para verificar a tiempo: quien llama responde 503 con Retry-After."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _hash_de_relleno() -> str:
    global _relleno
    if _relleno is None:
        _relleno = _hasher.hash(c.AUTH_DUMMY_PASSWORD)
    return _relleno


def verify_password(password_hash: str | None, password: str) -> bool:
    """True solo si coincide. Sin hash (usuario inexistente o sin login local) gasta el mismo tiempo y devuelve False.

    Lanza `VerificadorOcupado` si las verificaciones simultáneas están al tope más de `AUTH_HASH_WAIT_S`.
    """
    objetivo = password_hash or _hash_de_relleno()
    if not _semaforo.acquire(timeout=c.AUTH_HASH_WAIT_S):
        raise VerificadorOcupado()
    try:
        _hasher.verify(objetivo, password)
    except (VerificationError, InvalidHashError):
        return False
    finally:
        _semaforo.release()
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)

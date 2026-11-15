import logging
import secrets

from fastapi import Header, HTTPException

from app.core import config


def _from_header(value: str) -> str:
    """Starlette decodifica las cabeceras como latin-1; si venían en UTF-8, las recupera."""
    try:
        return value.encode(config.HEADER_ENCODING).decode(config.KEY_ENCODING)
    except UnicodeError:
        return value


def matches_api_key(candidate: str | None, expected: str) -> bool:
    """Compara en bytes (compare_digest falla con str no ASCII). Sin clave configurada o vacía: siempre False."""
    if not expected or not candidate:
        return False
    candidate = _from_header(candidate)
    return secrets.compare_digest(candidate.encode(config.KEY_ENCODING), expected.encode(config.KEY_ENCODING))


def is_valid_api_key(candidate: str | None) -> bool:
    """¿Es la clave de carga? Sin UPLOAD_API_KEY configurada: siempre False."""
    return matches_api_key(candidate, config.get_upload_api_key())


def require_api_key(x_api_key: str | None = Header(default=None, alias=config.API_KEY_HEADER)) -> None:
    """Exige X-API-Key. Si UPLOAD_API_KEY no está definida, la carga queda cerrada (401)."""
    if not is_valid_api_key(x_api_key):
        raise HTTPException(status_code=config.HTTP_UNAUTHORIZED, detail=config.MSG_API_KEY_INVALID)


def require_admin_key(x_api_key: str | None = Header(default=None, alias=config.API_KEY_HEADER)) -> None:
    """Exige la clave de administración. Cerrado (401) sin ADMIN_API_KEY o si es igual a UPLOAD_API_KEY."""
    expected = config.get_admin_api_key()
    if expected and expected == config.get_upload_api_key():
        logging.getLogger(config.KEYS_LOGGER).error(config.MSG_KEYS_SAME_KEY_LOG)
        raise HTTPException(status_code=config.HTTP_UNAUTHORIZED, detail=config.MSG_KEYS_ADMIN_KEY)
    if not matches_api_key(x_api_key, expected):
        raise HTTPException(status_code=config.HTTP_UNAUTHORIZED, detail=config.MSG_KEYS_ADMIN_KEY)

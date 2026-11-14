import secrets

from fastapi import Header, HTTPException

from app.core import config


def _from_header(value: str) -> str:
    """Starlette decodifica las cabeceras como latin-1; si venían en UTF-8, las recupera."""
    try:
        return value.encode(config.HEADER_ENCODING).decode(config.KEY_ENCODING)
    except UnicodeError:
        return value


def is_valid_api_key(candidate: str | None) -> bool:
    """Compara en bytes (compare_digest falla con str no ASCII). Sin clave configurada: siempre False."""
    expected = config.get_upload_api_key()
    if not expected or not candidate:
        return False
    candidate = _from_header(candidate)
    return secrets.compare_digest(candidate.encode(config.KEY_ENCODING), expected.encode(config.KEY_ENCODING))


def require_api_key(x_api_key: str | None = Header(default=None, alias=config.API_KEY_HEADER)) -> None:
    """Exige X-API-Key. Si UPLOAD_API_KEY no está definida, la carga queda cerrada (401)."""
    if not is_valid_api_key(x_api_key):
        raise HTTPException(status_code=config.HTTP_UNAUTHORIZED, detail=config.MSG_API_KEY_INVALID)

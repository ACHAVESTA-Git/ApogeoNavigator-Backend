import secrets

from fastapi import Header, HTTPException

from app.core import config


def require_api_key(x_api_key: str | None = Header(default=None, alias=config.API_KEY_HEADER)) -> None:
    """Exige X-API-Key. Si UPLOAD_API_KEY no está definida, la carga queda cerrada (401)."""
    expected = config.get_upload_api_key()
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=config.HTTP_UNAUTHORIZED, detail=config.MSG_API_KEY_INVALID)

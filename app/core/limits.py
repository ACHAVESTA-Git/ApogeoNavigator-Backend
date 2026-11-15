"""Guardia previa: rechaza subidas no autorizadas o demasiado grandes sin leer el cuerpo."""
from fastapi import Request

from app.core import config
from app.core.respuestas import respuesta_error
from app.core.security import is_valid_api_key


async def upload_guard(request: Request, call_next):
    if request.url.path.startswith(config.UPLOAD_ROUTER_PREFIX) and request.method == "POST":
        if not is_valid_api_key(request.headers.get(config.API_KEY_HEADER)):
            return respuesta_error(request, config.HTTP_UNAUTHORIZED, config.MSG_API_KEY_INVALID)
        declared = request.headers.get(config.CONTENT_LENGTH_HEADER, "")
        if declared.isdigit() and int(declared) > config.MAX_FILE_BYTES + config.MULTIPART_OVERHEAD_BYTES:
            return respuesta_error(request, config.HTTP_BY_CODE[config.E_TAMANO], config.MSG_TOO_BIG.format(mb=config.MAX_FILE_MB),
                                   extra={"codigo": config.E_TAMANO})
    return await call_next(request)

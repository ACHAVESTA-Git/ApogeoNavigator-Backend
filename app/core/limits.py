"""Guardia previa de las subidas: autentica y limita el tamaño ANTES de que se lea el cuerpo.

Middleware ASGI puro (no BaseHTTPMiddleware) para poder contar los bytes que llegan aunque no haya Content-Length: un POST
en trozos (chunked) sin cabecera se corta al pasar el límite y no llega entero a memoria (en Cloud Run /tmp es memoria).
La autenticación es real: API key válida o una sesión vigente en la base; una cookie inventada no salta el guardia.
"""
import anyio
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import config
from app.core.respuestas import respuesta_error
from app.core.security import is_valid_api_key


def _respuesta_tamano(request: Request):
    return respuesta_error(request, config.HTTP_BY_CODE[config.E_TAMANO], config.MSG_TOO_BIG.format(mb=config.MAX_FILE_MB),
                           extra={"codigo": config.E_TAMANO})


async def _autorizado(request: Request) -> bool:
    if is_valid_api_key(request.headers.get(config.API_KEY_HEADER)):
        return True
    token = request.cookies.get(config.AUTH_COOKIE_NAME)
    if not token:
        return False
    from app.api.deps import sesion_valida        # import tardío: deps importa servicios que importan core
    return await anyio.to_thread.run_sync(sesion_valida, token)


class UploadGuard:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith(config.UPLOAD_ROUTER_PREFIX):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if not await _autorizado(request):
            await respuesta_error(request, config.HTTP_UNAUTHORIZED, config.MSG_API_KEY_INVALID)(scope, receive, send)
            return
        limite = config.MAX_FILE_BYTES + config.MULTIPART_OVERHEAD_BYTES
        declarado = request.headers.get(config.CONTENT_LENGTH_HEADER, "")
        if declarado.isdigit() and int(declarado) > limite:
            await _respuesta_tamano(request)(scope, receive, send)
            return
        recibidos, excedido = 0, False

        async def recibir():
            nonlocal recibidos, excedido
            if excedido:
                return {"type": "http.disconnect"}
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibidos += len(mensaje.get("body", b""))
                if recibidos > limite:                # sin Content-Length (o mintiendo): se deja de leer aquí
                    excedido = True
                    return {"type": "http.disconnect"}
            return mensaje

        async def enviar(mensaje):
            if not excedido:                          # si se pasó del límite, lo que conteste la app se descarta
                await send(mensaje)

        try:
            await self.app(scope, recibir, enviar)
        except Exception:
            if not excedido:
                raise
        if excedido:
            await _respuesta_tamano(request)(scope, receive, send)

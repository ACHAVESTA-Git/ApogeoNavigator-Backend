"""Middleware de petición: X-Request-ID en TODAS las respuestas, línea de log por petición y 500 en JSON para lo imprevisto."""
import logging

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import config as c
from app.core.logging_config import ahora_ms, registrar_acceso
from app.core.respuestas import sanear_request_id

log = logging.getLogger(c.LOGGER_ACCESS)


class RequestContextMiddleware:
    """ASGI puro. Va por dentro de CORS (así hasta el 500 lleva sus cabeceras) y por fuera de todo lo demás.

    - Reutiliza el X-Request-ID del cliente si es seguro; si no, genera uno.
    - Añade X-Request-ID a cada respuesta, también a las 2xx.
    - Registra una línea por petición con el estado y, si hubo error, su código del catálogo y la acción sugerida.
    - Si una excepción llega hasta aquí sin manejar, responde un 500 JSON estandarizado (la traza solo va al log).
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        solicitado = next((v.decode("latin-1") for k, v in scope["headers"] if k == c.REQUEST_ID_HEADER.lower().encode()), None)
        rid = sanear_request_id(solicitado)
        estado = scope.setdefault("state", {})
        estado["request_id"] = rid
        inicio = ahora_ms()
        empezo = False

        async def enviar(message):
            nonlocal empezo
            if message["type"] == "http.response.start":
                empezo = True
                cabeceras = MutableHeaders(scope=message)
                if c.REQUEST_ID_HEADER not in cabeceras:
                    cabeceras[c.REQUEST_ID_HEADER] = rid
                registrar_acceso(log, rid, scope["method"], scope["path"], message["status"], estado.get("error_codigo"),
                                 ahora_ms() - inicio, estado.get("error_accion"))
            await send(message)

        try:
            await self.app(scope, receive, enviar)
        except Exception as exc:  # noqa: BLE001  (último recurso: nada imprevisto sale como texto plano ni con la traza)
            if empezo:
                raise
            from app.core.errors import unhandled
            respuesta = await unhandled(Request(scope, receive), exc)
            await respuesta(scope, receive, enviar)

"""Middleware de cabeceras de seguridad: las mismas en TODAS las respuestas, también en errores y rechazos."""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import config as c


def _sin_cache(ruta: str) -> bool:
    """True si la ruta es una de las que nunca se cachean (el prefijo solo, o una ruta por debajo de él)."""
    return any(ruta == prefijo or ruta.startswith(prefijo + "/") for prefijo in c.NO_STORE_PATH_PREFIXES)


class SecurityHeadersMiddleware:
    """ASGI puro, en el mismo estilo que `RequestContextMiddleware`.

    - Añade `c.SECURITY_HEADERS` a cada respuesta.
    - Añade HSTS solo si `c.is_production()` (se evalúa por petición, igual que el resto de la configuración).
    - Pone `Cache-Control: no-store` en las rutas de `c.NO_STORE_PATH_PREFIXES`, aunque la ruta ya tuviera otro valor.
    - No añade CSP: Swagger (`/docs`) necesita cargar sus recursos.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ruta = scope["path"]

        async def enviar(message: Message) -> None:
            if message["type"] == "http.response.start":
                cabeceras = MutableHeaders(scope=message)
                for nombre, valor in c.SECURITY_HEADERS:
                    cabeceras[nombre] = valor
                if c.is_production():
                    cabeceras[c.HSTS_HEADER] = c.HSTS_VALUE
                if _sin_cache(ruta):
                    cabeceras[c.CACHE_CONTROL_HEADER] = c.CACHE_CONTROL_NO_STORE
            await send(message)

        await self.app(scope, receive, enviar)

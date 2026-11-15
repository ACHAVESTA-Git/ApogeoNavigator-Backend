"""Construcción de las respuestas de error estandarizadas: `detail` intacto + objeto `error` + cabecera X-Request-ID."""
import re
import secrets
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core import config as c
from app.core import mensajes
from app.utils.text import clean_text

_REQUEST_ID_RE = re.compile(c.REQUEST_ID_RE)
_SAFE_ID_RE = re.compile(c.CONTEXT_SAFE_ID_RE)


def nuevo_request_id() -> str:
    return secrets.token_hex(c.REQUEST_ID_BYTES)


def sanear_request_id(valor: str | None) -> str:
    """Reutiliza el X-Request-ID del cliente solo si es seguro (letras, dígitos, . _ -); si no, genera uno nuevo."""
    return valor if valor and _REQUEST_ID_RE.fullmatch(valor) else nuevo_request_id()


def request_id_de(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = nuevo_request_id()
        request.state.request_id = rid
    return rid


def ahora_utc() -> str:
    return datetime.now(timezone.utc).strftime(c.ERROR_TS_FORMAT)


def _sitio(request: Request) -> str:
    """Identificador(es) de sitio que citó el cliente, SOLO si parecen identificadores (letras, dígitos, _ y -).

    Cualquier otra cosa (rutas, etiquetas HTML, caracteres de control) no se refleja en la respuesta.
    """
    valores = [request.path_params.get("site_id")] if request.path_params.get("site_id") else request.query_params.getlist("site_id")
    seguros = [v for v in valores if _SAFE_ID_RE.fullmatch(v)]
    return ", ".join(seguros) if seguros and len(seguros) == len(valores) else c.CONTEXT_MISSING


def respuesta_error(request: Request, estado: int, detail, *, codigo: str | None = None, resumen: str | None = None,
                    mensaje: str | None = None, headers: dict | None = None, extra: dict | None = None) -> JSONResponse:
    """Respuesta JSON de error: {"detail": detail, ...extra, "error": {codigo, estado, titulo, mensaje, accion, request_id, ts_utc}}.

    `detail` conserva el valor y la forma de siempre. Nada del interior del servidor (trazas, SQL, rutas) entra aquí.
    """
    cod = mensajes.codigo_para(estado, detail, codigo)
    contexto = {"sitio": _sitio(request), "detalle": clean_text(detail, 300) if isinstance(detail, str) else "", "resumen": resumen or ""}
    rid = request_id_de(request)
    error = {**mensajes.construir(cod, contexto), "estado": estado, "request_id": rid, "ts_utc": ahora_utc()}
    if mensaje:
        error["mensaje"] = mensaje
    request.state.error_codigo, request.state.error_accion = cod, error["accion"]
    cuerpo = {"detail": detail, **(extra or {}), "error": error}
    return JSONResponse(status_code=estado, content=cuerpo, headers={**(headers or {}), c.REQUEST_ID_HEADER: rid})

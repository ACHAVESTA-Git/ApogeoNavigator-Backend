"""Gestor de claves PQC (módulo de admin). Política «revocar y reemitir».

- La clave PRIVADA se entrega UNA sola vez en la respuesta de generar o rotar; el servidor no la guarda y
  ningún endpoint la vuelve a devolver. El admin no la ve: la recibe quien hace la petición para entregársela
  al usuario, y si se pierde se revoca y se emite otra.
- Autenticación: X-API-Key con ADMIN_API_KEY, que debe ser distinta de UPLOAD_API_KEY (si son iguales, el gestor
  queda cerrado), hasta que exista OAuth con rol admin.
- Cada cambio (generar, revocar, rotar) se registra en la auditoría firmada en la misma transacción: si no se
  puede registrar, no se hace. Las LECTURAS (listar, pública, huella) no entran en la cadena firmada porque
  `accion_auditoria` no tiene un valor de lectura; dejan una línea de log estructurado (sin emails).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from app.api.deps import autenticar_admin, get_claves_service
from app.core import config as c
from app.mappers.claves import nombre_archivo_publica
from app.models.claves import EstadoClave, TipoClave
from app.schemas.claves import (FORMATO_DESCRIPTION, PUBLICA_DESCRIPTION, PUBLICA_PEM_CONTENT, ClaveOut, Entrega, GenerarIn,
                                HuellaOut, PaginaClaves, PublicaOut, RevocarIn, RotarIn)
from app.services.claves import ClavesService, EntregaPendiente

router = APIRouter(prefix=c.KEYS_ROUTE_PREFIX, tags=[c.KEYS_ROUTER_TAG], dependencies=[Depends(autenticar_admin)])


# ---------------------------------------------------------------- endpoints
def _respuesta_entrega(pendiente: EntregaPendiente, servicio: ClavesService) -> JSONResponse:
    """La privada viaja una sola vez; el contador de entrega y el borrado de memoria corren tras enviar la respuesta."""
    return JSONResponse(content=pendiente.entrega.model_dump(mode="json"), status_code=c.HTTP_CREATED,
                        headers=c.NO_STORE_HEADERS, background=BackgroundTask(servicio.cerrar_entrega, pendiente))


@router.get("", response_model=PaginaClaves, summary="List keys (filters and pagination)")
def listar(usuario_id: uuid.UUID | None = None, estado: EstadoClave | None = None, tipo: TipoClave | None = None,
           q: str | None = Query(None, max_length=c.KEYS_Q_MAX, description="Text in the user name, or the full email (exact match: the email is stored encrypted)"),
           limite: int = Query(c.KEYS_PAGE_DEFAULT, ge=1, le=c.KEYS_PAGE_MAX), offset: int = Query(0, ge=0),
           servicio: ClavesService = Depends(get_claves_service)):
    return servicio.listar(usuario_id, estado, tipo, q, limite, offset)


@router.post("", status_code=c.HTTP_CREATED, response_model=Entrega, summary="Generate a key pair for a user (private key delivered once)")
def generar(cuerpo: GenerarIn, servicio: ClavesService = Depends(get_claves_service)):
    return _respuesta_entrega(servicio.generar(cuerpo), servicio)


@router.get("/{clave_id}/publica", response_model=PublicaOut, summary="Public key (JSON or PEM)",
            responses={c.HTTP_OK: {"description": PUBLICA_DESCRIPTION, "content": {c.PEM_MEDIA_TYPE: PUBLICA_PEM_CONTENT}}})
def publica(clave_id: uuid.UUID, formato: str = Query(c.KEYS_FORMAT_JSON, pattern=c.KEYS_FORMAT_PATTERN, description=FORMATO_DESCRIPTION),
            servicio: ClavesService = Depends(get_claves_service)):
    """Pública (también si está revocada: sirve para verificar lo firmado antes)."""
    salida = servicio.publica(clave_id, formato)
    if formato == c.KEYS_FORMAT_PEM:
        return Response(content=salida.clave_publica_pem, media_type=c.PEM_MEDIA_TYPE,
                        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo_publica(salida)}"'})
    return salida


@router.get("/{clave_id}/huella", response_model=HuellaOut, summary="SHA3-256 fingerprint of a public key")
def huella(clave_id: uuid.UUID, servicio: ClavesService = Depends(get_claves_service)):
    return servicio.huella(clave_id)


@router.post("/{clave_id}/revocar", response_model=ClaveOut, summary="Revoke a key with a reason")
def revocar(clave_id: uuid.UUID, cuerpo: RevocarIn, servicio: ClavesService = Depends(get_claves_service)):
    return servicio.revocar(clave_id, cuerpo)


@router.post("/{clave_id}/rotar", status_code=c.HTTP_CREATED, response_model=Entrega, summary="Revoke a key and issue its replacement")
def rotar(clave_id: uuid.UUID, cuerpo: RotarIn | None = None, servicio: ClavesService = Depends(get_claves_service)):
    """Revoca la clave y emite otra del mismo tipo para el mismo usuario (la privada nueva se entrega una vez)."""
    return _respuesta_entrega(servicio.rotar(clave_id, cuerpo), servicio)

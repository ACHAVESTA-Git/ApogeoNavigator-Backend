"""Gestor de claves PQC (módulo de admin). Política «revocar y reemitir».

- La clave PRIVADA se entrega UNA sola vez en la respuesta de generar o rotar; el servidor no la guarda y
  ningún endpoint la vuelve a devolver. El admin no la ve: la recibe quien hace la petición para entregársela
  al usuario, y si se pierde se revoca y se emite otra.
- Autenticación: X-API-Key con ADMIN_API_KEY (distinta de la de carga) hasta que exista OAuth con rol admin.
- Cada acción se registra en la auditoría firmada (CAMBIO_DATOS) en la misma transacción: si no se puede
  registrar, no se hace.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core import config as app_config
from app.db import session as db_session
from app.db.enums import AccionAuditoria
from app.db.models import Usuario
from app.db.session import get_db
from app.models.claves import ClaveUsuario, EstadoClave, TipoClave
from app.security import gestor_claves as gk
from app.utils import auditoria_util

log = logging.getLogger("apogeo.claves")

ENV_ADMIN_API_KEY = "ADMIN_API_KEY"
HEADER_API_KEY = app_config.API_KEY_HEADER
PREFIX = "/api/admin/claves"
ACTOR_ADMIN = "admin-api-key"
PAGE_DEFAULT = 50
PAGE_MAX = 200
MOTIVE_MIN = 3
MOTIVE_ROTATION = "rotación"
ESCAPE = "\\"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
PEM_MEDIA_TYPE = "application/x-pem-file"
FORMAT_PEM = "pem"
FORMAT_JSON = "json"

OP_GENERADA = "clave_generada"
OP_REVOCADA = "clave_revocada"
OP_ROTADA = "clave_rotada"

MSG_ADMIN_KEY = "Clave de administración inválida o ausente"
MSG_NO_USER = "El usuario no existe"
MSG_INACTIVE_USER = "El usuario está desactivado; no se le pueden emitir claves"
MSG_NO_KEY = "La clave no existe"
MSG_ALREADY_ACTIVE = "El usuario ya tiene una clave activa de tipo {tipo}; usa rotar para reemitirla"
MSG_ALREADY_ACTIVE_GENERIC = "Ya hay una clave activa para ese usuario y tipo"
MSG_ALREADY_REVOKED = "La clave ya está revocada"
MSG_AUDIT_FAILED = "No se pudo registrar la auditoría; no se hizo ningún cambio"
MSG_WARNING = ("Guarda la clave privada ahora: se muestra UNA sola vez y no se puede recuperar. "
               "Si se pierde, hay que revocar el par y emitir uno nuevo.")



# ---------------------------------------------------------------- autenticación
def require_admin_key(x_api_key: str | None = Header(default=None, alias=HEADER_API_KEY)) -> None:
    """Exige la clave de administración. Sin ADMIN_API_KEY configurada, el gestor queda cerrado (401)."""
    expected = os.getenv(ENV_ADMIN_API_KEY, "")
    if not expected or not x_api_key:
        raise HTTPException(status_code=401, detail=MSG_ADMIN_KEY)
    try:
        candidate = x_api_key.encode(app_config.HEADER_ENCODING).decode(app_config.KEY_ENCODING)
    except UnicodeError:
        candidate = x_api_key
    if not secrets.compare_digest(candidate.encode(app_config.KEY_ENCODING), expected.encode(app_config.KEY_ENCODING)):
        raise HTTPException(status_code=401, detail=MSG_ADMIN_KEY)


router = APIRouter(prefix=PREFIX, tags=["admin-claves"], dependencies=[Depends(require_admin_key)])


# ---------------------------------------------------------------- esquemas
class ClaveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    usuario_id: uuid.UUID
    usuario_email: str | None = None
    usuario_nombre: str | None = None
    tipo: TipoClave
    algoritmo: str
    estado: EstadoClave
    huella_sha3_256: str
    tamano_publica: int
    tamano_privada: int = Field(description="Bytes de la privada entregada (semilla); no se guarda")
    tamano_privada_fips: int = Field(description="Tamaño de la clave privada expandida según el estándar")
    creada_utc: datetime
    revocada_utc: datetime | None
    motivo_revocacion: str | None
    reemplazada_por: uuid.UUID | None
    backups: int = Field(description="Copias de la privada entregadas: 0 (sin entregar) o 1")


class PaginaClaves(BaseModel):
    total: int
    limite: int
    offset: int
    items: list[ClaveOut]


class ClaveEntregada(ClaveOut):
    clave_publica_pem: str
    privada_pem: str = Field(description="Se entrega UNA sola vez; no se puede recuperar")
    nombre_archivo_privada: str


class Entrega(BaseModel):
    aviso: str
    claves: list[ClaveEntregada]


class GenerarIn(BaseModel):
    usuario_id: uuid.UUID
    tipos: list[TipoClave] = Field(default_factory=lambda: [TipoClave.KEM, TipoClave.FIRMA], min_length=1, max_length=2)


class RevocarIn(BaseModel):
    motivo: str = Field(min_length=MOTIVE_MIN, max_length=200)


class RotarIn(BaseModel):
    motivo: str = Field(default=MOTIVE_ROTATION, min_length=MOTIVE_MIN, max_length=200)


class PublicaOut(BaseModel):
    id: uuid.UUID
    tipo: TipoClave
    algoritmo: str
    estado: EstadoClave
    huella_sha3_256: str
    clave_publica_pem: str
    clave_publica_b64: str


class HuellaOut(BaseModel):
    id: uuid.UUID
    algoritmo: str
    estado: EstadoClave
    huella_sha3_256: str


# ---------------------------------------------------------------- utilidades
def _out(clave: ClaveUsuario, usuario: Usuario | None = None) -> dict:
    """Campos públicos de una clave. Nunca incluye material privado (no existe en la base)."""
    return {"id": clave.id, "usuario_id": clave.usuario_id, "tipo": clave.tipo, "algoritmo": clave.algoritmo,
            "estado": clave.estado, "huella_sha3_256": clave.huella_sha3_256, "tamano_publica": clave.tamano_publica,
            "tamano_privada": clave.tamano_privada, "tamano_privada_fips": gk.TAMANO_PRIVADA_FIPS[clave.tipo.value],
            "creada_utc": clave.creada_utc, "revocada_utc": clave.revocada_utc,
            "motivo_revocacion": clave.motivo_revocacion, "reemplazada_por": clave.reemplazada_por,
            "backups": clave.backups,
            "usuario_email": usuario.email if usuario else None, "usuario_nombre": usuario.nombre if usuario else None}


def _auditar(db: Session, operacion: str, clave: ClaveUsuario, motivo: str | None = None, extra: dict | None = None) -> None:
    detalle = {"operacion": operacion, "clave_id": str(clave.id), "usuario_id": str(clave.usuario_id),
               "tipo": clave.tipo.value, "algoritmo": clave.algoritmo, "huella": clave.huella_sha3_256}
    if motivo:
        detalle["motivo"] = motivo
    detalle.update(extra or {})
    try:
        auditoria_util.registrar_evento(AccionAuditoria.CAMBIO_DATOS, ACTOR_ADMIN, recurso=f"clave/{clave.id}",
                                        detalle=detalle, db=db, estricto=True)
    except auditoria_util.ErrorAuditoria:
        raise HTTPException(status_code=503, detail=MSG_AUDIT_FAILED) from None


def _nueva_clave(db: Session, usuario: Usuario, tipo: str) -> tuple[ClaveUsuario, gk.ParNuevo]:
    par = gk.generar_par(tipo)
    clave = ClaveUsuario(id=uuid.uuid4(), usuario_id=usuario.id, tipo=TipoClave(tipo), algoritmo=par.algoritmo,
                         clave_publica=par.publica, huella_sha3_256=par.huella, tamano_publica=par.tamano_publica,
                         tamano_privada=par.tamano_privada, estado=EstadoClave.ACTIVA, backups=0)
    db.add(clave)
    return clave, par


def _entregar(db: Session, usuario: Usuario, creadas: list[tuple[ClaveUsuario, gk.ParNuevo]]) -> JSONResponse:
    """Confirma la transacción y devuelve las privadas UNA vez. Tras enviar la respuesta marca backups=1 y borra."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        for _, par in creadas:
            gk.borrar(par.privada_pem)
        raise HTTPException(status_code=409, detail=MSG_ALREADY_ACTIVE_GENERIC) from None
    claves = []
    for clave, par in creadas:
        item = _out(clave, usuario)
        item |= {"clave_publica_pem": gk.publica_pem(clave.tipo.value, clave.clave_publica),
                 "privada_pem": par.privada_pem.decode("ascii"),
                 "nombre_archivo_privada": f"{clave.tipo.value}-{clave.huella_sha3_256[:12]}.privada.pem"}
        claves.append(item)
    cuerpo = Entrega(aviso=MSG_WARNING, claves=[ClaveEntregada(**c) for c in claves]).model_dump(mode="json")
    ids = [clave.id for clave, _ in creadas]
    buffers = [par.privada_pem for _, par in creadas]
    return JSONResponse(content=cuerpo, status_code=201, headers=NO_STORE,
                        background=BackgroundTask(_marcar_entregadas, ids, buffers))


def _marcar_entregadas(ids: list[uuid.UUID], buffers: list[bytearray]) -> None:
    """Se ejecuta DESPUÉS de enviar la respuesta: cuenta la entrega (backups=1) y borra la privada de memoria."""
    try:
        with db_session.session_scope() as session:
            for clave in session.scalars(select(ClaveUsuario).where(ClaveUsuario.id.in_(ids))):
                clave.backups = 1
    except Exception as exc:  # noqa: BLE001  (la entrega ya ocurrió; solo se registra el fallo del contador)
        log.error("no se pudo contar la entrega de la clave (%s)", type(exc).__name__)
    finally:
        for buffer in buffers:
            gk.borrar(buffer)


def _get(db: Session, clave_id: uuid.UUID) -> ClaveUsuario:
    clave = db.get(ClaveUsuario, clave_id)
    if clave is None:
        raise HTTPException(status_code=404, detail=MSG_NO_KEY)
    return clave


def _usuario_activo(db: Session, usuario_id: uuid.UUID) -> Usuario:
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail=MSG_NO_USER)
    if not usuario.activo:
        raise HTTPException(status_code=409, detail=MSG_INACTIVE_USER)
    return usuario


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- endpoints
@router.get("", response_model=PaginaClaves)
def listar(usuario_id: uuid.UUID | None = None, estado: EstadoClave | None = None, tipo: TipoClave | None = None,
           q: str | None = Query(None, max_length=100, description="Texto en el email o el nombre del usuario"),
           limite: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX), offset: int = Query(0, ge=0),
           db: Session = Depends(get_db)):
    consulta = select(ClaveUsuario, Usuario).join(Usuario, Usuario.id == ClaveUsuario.usuario_id)
    if usuario_id:
        consulta = consulta.where(ClaveUsuario.usuario_id == usuario_id)
    if estado:
        consulta = consulta.where(ClaveUsuario.estado == estado)
    if tipo:
        consulta = consulta.where(ClaveUsuario.tipo == tipo)
    if q:
        patron = "%" + q.replace(ESCAPE, ESCAPE * 2).replace("%", ESCAPE + "%").replace("_", ESCAPE + "_") + "%"
        consulta = consulta.where(or_(Usuario.email.ilike(patron, escape=ESCAPE), Usuario.nombre.ilike(patron, escape=ESCAPE)))
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    filas = db.execute(consulta.order_by(ClaveUsuario.creada_utc.desc(), ClaveUsuario.id).limit(limite).offset(offset)).all()
    return PaginaClaves(total=total, limite=limite, offset=offset, items=[ClaveOut(**_out(c, u)) for c, u in filas])


@router.post("", status_code=201, response_model=Entrega)
def generar(cuerpo: GenerarIn, db: Session = Depends(get_db)):
    usuario = _usuario_activo(db, cuerpo.usuario_id)
    tipos = list(dict.fromkeys(cuerpo.tipos))
    for tipo in tipos:
        existe = db.scalar(select(ClaveUsuario.id).where(
            ClaveUsuario.usuario_id == usuario.id, ClaveUsuario.tipo == tipo, ClaveUsuario.estado == EstadoClave.ACTIVA))
        if existe:
            raise HTTPException(status_code=409, detail=MSG_ALREADY_ACTIVE.format(tipo=tipo.value))
    creadas = [_nueva_clave(db, usuario, tipo.value) for tipo in tipos]
    try:
        db.flush()
        for clave, _ in creadas:
            _auditar(db, OP_GENERADA, clave)
    except BaseException:
        for _, par in creadas:
            gk.borrar(par.privada_pem)
        raise
    return _entregar(db, usuario, creadas)


@router.get("/{clave_id}/publica", response_model=PublicaOut)
def publica(clave_id: uuid.UUID, formato: str = Query(FORMAT_JSON, pattern=f"^({FORMAT_JSON}|{FORMAT_PEM})$"),
            db: Session = Depends(get_db)):
    """Pública (también si está revocada: sirve para verificar lo firmado antes)."""
    clave = _get(db, clave_id)
    pem = gk.publica_pem(clave.tipo.value, clave.clave_publica)
    if formato == FORMAT_PEM:
        return Response(content=pem, media_type=PEM_MEDIA_TYPE,
                        headers={"Content-Disposition": f'attachment; filename="{clave.tipo.value}-{clave.huella_sha3_256[:12]}.publica.pem"'})
    return PublicaOut(id=clave.id, tipo=clave.tipo, algoritmo=clave.algoritmo, estado=clave.estado,
                      huella_sha3_256=clave.huella_sha3_256, clave_publica_pem=pem,
                      clave_publica_b64=base64.b64encode(clave.clave_publica).decode("ascii"))


@router.get("/{clave_id}/huella", response_model=HuellaOut)
def huella(clave_id: uuid.UUID, db: Session = Depends(get_db)):
    clave = _get(db, clave_id)
    return HuellaOut(id=clave.id, algoritmo=clave.algoritmo, estado=clave.estado, huella_sha3_256=clave.huella_sha3_256)


@router.post("/{clave_id}/revocar", response_model=ClaveOut)
def revocar(clave_id: uuid.UUID, cuerpo: RevocarIn, db: Session = Depends(get_db)):
    clave = _get(db, clave_id)
    if clave.estado == EstadoClave.REVOCADA:
        raise HTTPException(status_code=409, detail=MSG_ALREADY_REVOKED)
    clave.estado, clave.revocada_utc, clave.motivo_revocacion = EstadoClave.REVOCADA, _ahora(), cuerpo.motivo
    db.flush()
    _auditar(db, OP_REVOCADA, clave, motivo=cuerpo.motivo)
    usuario = db.get(Usuario, clave.usuario_id)
    db.commit()
    return ClaveOut(**_out(clave, usuario))


@router.post("/{clave_id}/rotar", status_code=201, response_model=Entrega)
def rotar(clave_id: uuid.UUID, cuerpo: RotarIn | None = None, db: Session = Depends(get_db)):
    """Revoca la clave y emite otra del mismo tipo para el mismo usuario (la privada nueva se entrega una vez)."""
    motivo = (cuerpo or RotarIn()).motivo
    vieja = _get(db, clave_id)
    if vieja.estado == EstadoClave.REVOCADA:
        raise HTTPException(status_code=409, detail=MSG_ALREADY_REVOKED)
    usuario = _usuario_activo(db, vieja.usuario_id)
    vieja.estado, vieja.revocada_utc, vieja.motivo_revocacion = EstadoClave.REVOCADA, _ahora(), motivo
    db.flush()                                    # libera el hueco del índice de «una activa por tipo»
    nueva, par = _nueva_clave(db, usuario, vieja.tipo.value)
    try:
        db.flush()
        vieja.reemplazada_por = nueva.id
        db.flush()
        _auditar(db, OP_ROTADA, nueva, motivo=motivo, extra={"reemplaza_a": str(vieja.id), "huella_anterior": vieja.huella_sha3_256})
    except BaseException:
        gk.borrar(par.privada_pem)
        raise
    return _entregar(db, usuario, [(nueva, par)])

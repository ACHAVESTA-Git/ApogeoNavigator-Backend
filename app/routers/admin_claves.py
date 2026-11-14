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

import base64
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core import config as c
from app.core.security import matches_api_key
from app.db import session as db_session
from app.db.enums import AccionAuditoria
from app.db.models import Usuario, utcnow
from app.db.session import get_db
from app.models.claves import ClaveUsuario, EstadoClave, TipoClave
from app.security import gestor_claves as gk
from app.utils import auditoria_util

log = logging.getLogger(c.KEYS_LOGGER)


# ---------------------------------------------------------------- autenticación
def require_admin_key(x_api_key: str | None = Header(default=None, alias=c.API_KEY_HEADER)) -> None:
    """Exige la clave de administración. Cerrado (401) sin ADMIN_API_KEY o si es igual a UPLOAD_API_KEY."""
    expected = c.get_admin_api_key()
    if expected and expected == c.get_upload_api_key():
        log.error(c.MSG_KEYS_SAME_KEY_LOG)
        raise HTTPException(status_code=c.HTTP_UNAUTHORIZED, detail=c.MSG_KEYS_ADMIN_KEY)
    if not matches_api_key(x_api_key, expected):
        raise HTTPException(status_code=c.HTTP_UNAUTHORIZED, detail=c.MSG_KEYS_ADMIN_KEY)


router = APIRouter(prefix=c.KEYS_ROUTE_PREFIX, tags=[c.KEYS_ROUTER_TAG], dependencies=[Depends(require_admin_key)])


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
    motivo: str = Field(min_length=c.KEYS_MOTIVE_MIN, max_length=c.KEYS_MOTIVE_MAX)


class RotarIn(BaseModel):
    motivo: str = Field(default=c.KEYS_MOTIVE_ROTATION, min_length=c.KEYS_MOTIVE_MIN, max_length=c.KEYS_MOTIVE_MAX)


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
def _email(usuario: Usuario | None) -> str | None:
    """Email descifrado del usuario (se guarda cifrado). None si no hay usuario o su clave ya no está configurada."""
    if usuario is None:
        return None
    try:
        return gk.descifrar_email(usuario.email_cifrado)
    except gk.ErrorGestorClaves:
        return None


def _out(clave: ClaveUsuario, usuario: Usuario | None = None) -> dict:
    """Campos públicos de una clave. Nunca incluye material privado (no existe en la base)."""
    return {"id": clave.id, "usuario_id": clave.usuario_id, "tipo": clave.tipo, "algoritmo": clave.algoritmo,
            "estado": clave.estado, "huella_sha3_256": clave.huella_sha3_256, "tamano_publica": clave.tamano_publica,
            "tamano_privada": clave.tamano_privada, "tamano_privada_fips": c.TAMANO_PRIVADA_FIPS[clave.tipo.value],
            "creada_utc": clave.creada_utc, "revocada_utc": clave.revocada_utc,
            "motivo_revocacion": clave.motivo_revocacion, "reemplazada_por": clave.reemplazada_por,
            "backups": clave.backups,
            "usuario_email": _email(usuario), "usuario_nombre": usuario.nombre if usuario else None}


def _auditar(db: Session, operacion: str, clave: ClaveUsuario, motivo: str | None = None, extra: dict | None = None) -> None:
    detalle = {"operacion": operacion, "clave_id": str(clave.id), "usuario_id": str(clave.usuario_id),
               "tipo": clave.tipo.value, "algoritmo": clave.algoritmo, "huella": clave.huella_sha3_256}
    if motivo:
        detalle["motivo"] = motivo
    detalle.update(extra or {})
    try:
        auditoria_util.registrar_evento(AccionAuditoria.CAMBIO_DATOS, c.ACTOR_ADMIN,
                                        recurso=c.KEYS_AUDIT_RESOURCE.format(id=clave.id), detalle=detalle, db=db,
                                        estricto=True)
    except auditoria_util.ErrorAuditoria:
        raise HTTPException(status_code=c.HTTP_UNAVAILABLE, detail=c.MSG_KEYS_AUDIT_FAILED) from None


def _log_lectura(operacion: str, filtros: str, resultados: int) -> None:
    """Las lecturas no entran en la cadena firmada: dejan rastro en el log (sin emails ni material de claves)."""
    log.info(c.MSG_KEYS_READ_LOG, operacion, filtros, resultados)


def _nueva_clave(db: Session, usuario: Usuario, tipo: str) -> tuple[ClaveUsuario, gk.ParNuevo]:
    par = gk.generar_par(tipo)
    clave = ClaveUsuario(id=uuid.uuid4(), usuario_id=usuario.id, tipo=TipoClave(tipo), algoritmo=par.algoritmo,
                         clave_publica=par.publica, huella_sha3_256=par.huella, tamano_publica=par.tamano_publica,
                         tamano_privada=par.tamano_privada, estado=EstadoClave.ACTIVA, backups=0)
    db.add(clave)
    return clave, par


def _borrar_todas(creadas: list[tuple[ClaveUsuario, gk.ParNuevo]]) -> None:
    for _, par in creadas:
        gk.borrar(par.privada_pem)


@contextmanager
def _cambio(db: Session, creadas: list[tuple[ClaveUsuario, gk.ParNuevo]]):
    """Protege una escritura: ante cualquier fallo borra las privadas generadas; un choque con el índice único
    «una activa por usuario y tipo» (dos peticiones simultáneas) es un 409, no un 500."""
    try:
        yield
    except IntegrityError:
        db.rollback()
        _borrar_todas(creadas)
        raise HTTPException(status_code=c.HTTP_CONFLICT, detail=c.MSG_KEYS_ALREADY_ACTIVE_GENERIC) from None
    except BaseException:
        _borrar_todas(creadas)
        raise


def _entregar(db: Session, usuario: Usuario, creadas: list[tuple[ClaveUsuario, gk.ParNuevo]]) -> JSONResponse:
    """Confirma la transacción y devuelve las privadas UNA vez. Tras enviar la respuesta marca backups=1 y borra."""
    with _cambio(db, creadas):
        db.commit()
    claves = []
    for clave, par in creadas:
        claves.append(_out(clave, usuario) | {
            "clave_publica_pem": gk.publica_pem(clave.tipo.value, clave.clave_publica),
            "privada_pem": par.privada_pem.decode("ascii"),
            "nombre_archivo_privada": c.KEYS_PRIVATE_FILENAME.format(
                tipo=clave.tipo.value, huella=clave.huella_sha3_256[:c.KEYS_FILE_FINGERPRINT_CHARS])})
    cuerpo = Entrega(aviso=c.MSG_KEYS_WARNING, claves=[ClaveEntregada(**item) for item in claves]).model_dump(mode="json")
    ids = [clave.id for clave, _ in creadas]
    buffers = [par.privada_pem for _, par in creadas]
    return JSONResponse(content=cuerpo, status_code=c.HTTP_CREATED, headers=c.NO_STORE_HEADERS,
                        background=BackgroundTask(_marcar_entregadas, ids, buffers))


def _marcar_entregadas(ids: list[uuid.UUID], buffers: list[bytearray]) -> None:
    """Se ejecuta DESPUÉS de enviar la respuesta: cuenta la entrega (backups=1) y borra la privada de memoria."""
    try:
        with db_session.session_scope() as session:
            for clave in session.scalars(select(ClaveUsuario).where(ClaveUsuario.id.in_(ids))):
                clave.backups = 1
    except Exception as exc:  # noqa: BLE001  (la entrega ya ocurrió; solo se registra el fallo del contador)
        log.error(c.MSG_KEYS_DELIVERY_COUNT_FAILED, type(exc).__name__)
    finally:
        for buffer in buffers:
            gk.borrar(buffer)


def _get(db: Session, clave_id: uuid.UUID, bloquear: bool = False) -> ClaveUsuario:
    """Busca la clave. Con ``bloquear`` toma un bloqueo de fila (SELECT ... FOR UPDATE; SQLite lo ignora)."""
    consulta = select(ClaveUsuario).where(ClaveUsuario.id == clave_id)
    clave = db.scalar(consulta.with_for_update() if bloquear else consulta)
    if clave is None:
        raise HTTPException(status_code=c.HTTP_NOT_FOUND, detail=c.MSG_KEYS_NO_KEY)
    return clave


def _usuario_activo(db: Session, usuario_id: uuid.UUID) -> Usuario:
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=c.HTTP_NOT_FOUND, detail=c.MSG_KEYS_NO_USER)
    if not usuario.activo:
        raise HTTPException(status_code=c.HTTP_CONFLICT, detail=c.MSG_KEYS_INACTIVE_USER)
    return usuario


# ---------------------------------------------------------------- endpoints
@router.get("", response_model=PaginaClaves)
def listar(usuario_id: uuid.UUID | None = None, estado: EstadoClave | None = None, tipo: TipoClave | None = None,
           q: str | None = Query(None, max_length=c.KEYS_Q_MAX, description="Texto en el nombre del usuario, o su email completo (exacto: el email se guarda cifrado)"),
           limite: int = Query(c.KEYS_PAGE_DEFAULT, ge=1, le=c.KEYS_PAGE_MAX), offset: int = Query(0, ge=0),
           db: Session = Depends(get_db)):
    consulta = select(ClaveUsuario, Usuario).join(Usuario, Usuario.id == ClaveUsuario.usuario_id)
    if usuario_id:
        consulta = consulta.where(ClaveUsuario.usuario_id == usuario_id)
    if estado:
        consulta = consulta.where(ClaveUsuario.estado == estado)
    if tipo:
        consulta = consulta.where(ClaveUsuario.tipo == tipo)
    if q:
        escape = c.LIKE_ESCAPE
        patron = "%" + q.replace(escape, escape * 2).replace("%", escape + "%").replace("_", escape + "_") + "%"
        consulta = consulta.where(or_(Usuario.email_hash.in_(gk.email_hash_candidatos(q)),
                                      Usuario.nombre.ilike(patron, escape=escape)))
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    filas = db.execute(consulta.order_by(ClaveUsuario.creada_utc.desc(), ClaveUsuario.id).limit(limite).offset(offset)).all()
    _log_lectura(c.OP_KEY_READ + ":listar",
                 f"usuario_id={bool(usuario_id)} estado={estado} tipo={tipo} q={bool(q)} limite={limite} offset={offset}",
                 len(filas))
    return PaginaClaves(total=total, limite=limite, offset=offset, items=[ClaveOut(**_out(k, u)) for k, u in filas])


@router.post("", status_code=c.HTTP_CREATED, response_model=Entrega)
def generar(cuerpo: GenerarIn, db: Session = Depends(get_db)):
    usuario = _usuario_activo(db, cuerpo.usuario_id)
    tipos = list(dict.fromkeys(cuerpo.tipos))
    for tipo in tipos:
        existe = db.scalar(select(ClaveUsuario.id).where(
            ClaveUsuario.usuario_id == usuario.id, ClaveUsuario.tipo == tipo, ClaveUsuario.estado == EstadoClave.ACTIVA))
        if existe:
            raise HTTPException(status_code=c.HTTP_CONFLICT, detail=c.MSG_KEYS_ALREADY_ACTIVE.format(tipo=tipo.value))
    creadas = [_nueva_clave(db, usuario, tipo.value) for tipo in tipos]
    with _cambio(db, creadas):
        db.flush()
        for clave, _ in creadas:
            _auditar(db, c.OP_KEY_GENERATED, clave)
    return _entregar(db, usuario, creadas)


@router.get("/{clave_id}/publica", response_model=PublicaOut)
def publica(clave_id: uuid.UUID, formato: str = Query(c.KEYS_FORMAT_JSON, pattern=c.KEYS_FORMAT_PATTERN),
            db: Session = Depends(get_db)):
    """Pública (también si está revocada: sirve para verificar lo firmado antes)."""
    clave = _get(db, clave_id)
    pem = gk.publica_pem(clave.tipo.value, clave.clave_publica)
    _log_lectura(c.OP_KEY_READ + ":publica", f"clave_id={clave.id} formato={formato}", 1)
    if formato == c.KEYS_FORMAT_PEM:
        nombre = c.KEYS_PUBLIC_FILENAME.format(tipo=clave.tipo.value,
                                               huella=clave.huella_sha3_256[:c.KEYS_FILE_FINGERPRINT_CHARS])
        return Response(content=pem, media_type=c.PEM_MEDIA_TYPE,
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
    return PublicaOut(id=clave.id, tipo=clave.tipo, algoritmo=clave.algoritmo, estado=clave.estado,
                      huella_sha3_256=clave.huella_sha3_256, clave_publica_pem=pem,
                      clave_publica_b64=base64.b64encode(clave.clave_publica).decode("ascii"))


@router.get("/{clave_id}/huella", response_model=HuellaOut)
def huella(clave_id: uuid.UUID, db: Session = Depends(get_db)):
    clave = _get(db, clave_id)
    _log_lectura(c.OP_KEY_READ + ":huella", f"clave_id={clave.id}", 1)
    return HuellaOut(id=clave.id, algoritmo=clave.algoritmo, estado=clave.estado, huella_sha3_256=clave.huella_sha3_256)


@router.post("/{clave_id}/revocar", response_model=ClaveOut)
def revocar(clave_id: uuid.UUID, cuerpo: RevocarIn, db: Session = Depends(get_db)):
    clave = _get(db, clave_id, bloquear=True)
    if clave.estado == EstadoClave.REVOCADA:
        raise HTTPException(status_code=c.HTTP_CONFLICT, detail=c.MSG_KEYS_ALREADY_REVOKED)
    clave.estado, clave.revocada_utc, clave.motivo_revocacion = EstadoClave.REVOCADA, utcnow(), cuerpo.motivo
    with _cambio(db, []):
        db.flush()
        _auditar(db, c.OP_KEY_REVOKED, clave, motivo=cuerpo.motivo)
    usuario = db.get(Usuario, clave.usuario_id)
    db.commit()
    return ClaveOut(**_out(clave, usuario))


@router.post("/{clave_id}/rotar", status_code=c.HTTP_CREATED, response_model=Entrega)
def rotar(clave_id: uuid.UUID, cuerpo: RotarIn | None = None, db: Session = Depends(get_db)):
    """Revoca la clave y emite otra del mismo tipo para el mismo usuario (la privada nueva se entrega una vez)."""
    motivo = (cuerpo or RotarIn()).motivo
    vieja = _get(db, clave_id, bloquear=True)
    if vieja.estado == EstadoClave.REVOCADA:
        raise HTTPException(status_code=c.HTTP_CONFLICT, detail=c.MSG_KEYS_ALREADY_REVOKED)
    usuario = _usuario_activo(db, vieja.usuario_id)
    vieja.estado, vieja.revocada_utc, vieja.motivo_revocacion = EstadoClave.REVOCADA, utcnow(), motivo
    creadas: list[tuple[ClaveUsuario, gk.ParNuevo]] = []
    with _cambio(db, creadas):
        db.flush()                                    # libera el hueco del índice de «una activa por tipo»
        creadas.append(_nueva_clave(db, usuario, vieja.tipo.value))
        nueva = creadas[0][0]
        db.flush()
        vieja.reemplazada_por = nueva.id
        db.flush()
        _auditar(db, c.OP_KEY_ROTATED, nueva, motivo=motivo,
                 extra={"reemplaza_a": str(vieja.id), "huella_anterior": vieja.huella_sha3_256})
    return _entregar(db, usuario, creadas)

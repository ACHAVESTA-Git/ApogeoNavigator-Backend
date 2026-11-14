"""auditoriaUtil: registro de eventos con cadena de hashes SHA3-256 y firma post-cuántica.

Reutilizable y desacoplado de las rutas: solo lee IP y User-Agent del `request`, si se pasa.

Cadena:  hash_actual = SHA3-256(hash_prev + evento canónico en JSON ordenado). El evento canónico incluye
         secuencia, momento UTC, usuario, actor, acción, recurso, detalle, ip_hash, user_agent, algoritmo
         de firma y huella de la clave: ninguno de esos campos se puede cambiar sin romper el hash.
Firma:   ML-DSA-65 (app/security/pqc.py), contexto b"auditoria", sobre el JSON canónico de
         {secuencia, hash_prev, hash_actual, ts_utc, usuario_id, accion, algoritmo, huella_clave}.
Fallo:   en producción (o AUDIT_REQUIRE_SIGNATURE=1) un evento que no se puede firmar no se registra.
         En desarrollo se guarda con algoritmo NINGUNO y verificar_cadena() lo marca como NO válido.
Mejor esfuerzo: si la base de datos no está configurada no se registra nada; si falla en desarrollo se
         anota un error en el log (nunca con la URL de la base) y la operación continúa.
"""
import hashlib
import hmac
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import func, select, text
from cryptography.exceptions import UnsupportedAlgorithm
from sqlalchemy import event as sa_event
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import config as c
from app.db import session as db_session
from app.db.enums import AccionAuditoria, TipoCarga
from app.db.models import CargaArchivo, EventoAuditoria
from app.security.pqc import ErrorClavesPQC, canonicalizar
from app.utils.text import clean_text

log = logging.getLogger("apogeo.auditoria")


class Firmante(Protocol):
    ALGORITMO: str

    def huella_clave_publica(self, clave: bytes | None = None) -> str: ...

    def firmar(self, datos: bytes, *, contexto: bytes = b"") -> bytes: ...

    def verificar(self, datos: bytes, firma: bytes, *, contexto: bytes = b"") -> bool: ...


class ErrorAuditoria(RuntimeError):
    """No se pudo registrar un evento (en producción se propaga y la API responde 503)."""


_UNSET = object()
_firmante: Any = _UNSET


def set_firmante(firmante: Firmante | None | object = _UNSET) -> None:
    """Inyecta el firmante (pruebas). Sin argumento vuelve a resolver app.security.pqc de forma perezosa."""
    global _firmante
    _firmante = firmante


def get_firmante() -> Firmante | None:
    global _firmante
    if _firmante is _UNSET:
        try:
            from app.security import pqc
            _firmante = pqc
        except ImportError:
            log.warning(c.MSG_AUDIT_NO_SIGNER)
            return None
    return _firmante


# ------------------------------------------------------------------ privacidad
def hash_ip(ip: str | None) -> str | None:
    """HMAC-SHA256(IP_HASH_SALT, ip) en hexadecimal. Sin sal o sin IP devuelve None: la IP nunca se guarda en claro.

    Es seudonimización, no anonimización: con una sal fija el espacio de IPv4 se podría recorrer por fuerza bruta.
    """
    salt = c.get_ip_hash_salt()
    if not ip or not salt:
        return None
    return hmac.new(salt.encode(), ip.encode(), hashlib.sha256).hexdigest()


def client_ip(request: Any) -> str | None:
    """IP del cliente: primer valor de X-Forwarded-For (Cloud Run/Firebase) o la del socket."""
    if request is None:
        return None
    forwarded = request.headers.get(c.FORWARDED_FOR_HEADER)
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if getattr(request, "client", None) else None


def _request_context(request: Any) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    return hash_ip(client_ip(request)), clean_text(request.headers.get(c.USER_AGENT_HEADER), c.DB_USER_AGENT_MAX)


# ------------------------------------------------------------------ canónico, hash y firma
def iso_utc(ts: datetime) -> str:
    """Fecha canónica en UTC (SQLite devuelve fechas sin zona: se tratan como UTC)."""
    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return ts.strftime(c.AUDIT_TS_FORMAT)


canonical_json = canonicalizar      # una sola definición del JSON canónico: la de app.security.pqc


def canonical_event(secuencia: int, ts_utc: datetime, usuario_id: uuid.UUID | None, actor: str, accion: str,
                    recurso: str | None, detalle: dict, ip_hash: str | None, user_agent: str | None,
                    algoritmo: str | None, huella_clave: str | None) -> bytes:
    """Única función que define el evento canónico (la usan la inserción y la verificación).

    `detalle` solo debe contener str, int, bool, None, listas y dicts (sin floats ni fechas).
    """
    return canonical_json({
        "secuencia": secuencia, "ts_utc": iso_utc(ts_utc), "usuario_id": str(usuario_id) if usuario_id else None,
        "actor": actor, "accion": str(accion), "recurso": recurso, "detalle": detalle, "ip_hash": ip_hash,
        "user_agent": user_agent, "algoritmo_firma": algoritmo, "huella_clave": huella_clave})


def chain_hash(hash_prev: str, canonical: bytes) -> str:
    return hashlib.sha3_256(hash_prev.encode("ascii") + canonical).hexdigest()


def event_signed_document(secuencia: int, hash_prev: str, hash_actual: str, ts_utc: datetime, usuario_id: uuid.UUID | None,
                          accion: str, algoritmo: str | None, huella_clave: str | None) -> dict:
    """Lo que cubre la firma de un evento: posición, enlace, huella del evento, momento, usuario, acción y clave."""
    return {"v": c.SIGNED_DOC_VERSION, "secuencia": secuencia, "hash_prev": hash_prev, "hash_actual": hash_actual,
            "ts_utc": iso_utc(ts_utc), "usuario_id": str(usuario_id) if usuario_id else None, "accion": str(accion),
            "algoritmo_firma": algoritmo, "huella_clave": huella_clave}


def _identity() -> tuple[Firmante | None, str, str | None]:
    """(firmante, algoritmo, huella). Sin firma posible: (None, 'NINGUNO', None) o ErrorAuditoria si es obligatoria."""
    signer = get_firmante()
    try:
        if signer is None:
            raise ErrorAuditoria(c.MSG_AUDIT_NO_SIGNER)
        return signer, signer.ALGORITMO, signer.huella_clave_publica()
    except (ErrorClavesPQC, ErrorAuditoria) as exc:
        if c.audit_requires_signature():
            raise ErrorAuditoria(c.MSG_AUDIT_SIGNATURE_REQUIRED) from exc
        log.warning("%s (%s)", c.MSG_AUDIT_NO_SIGNER, type(exc).__name__)
        return None, c.UNSIGNED_ALGORITHM, None


def _sign(signer: Firmante | None, document: dict, contexto: bytes) -> bytes | None:
    if signer is None:
        return None
    try:
        return signer.firmar(canonical_json(document), contexto=contexto)
    except (ErrorClavesPQC, UnsupportedAlgorithm, ValueError) as exc:
        raise ErrorAuditoria(c.MSG_AUDIT_SIGNATURE_REQUIRED) from exc


# ------------------------------------------------------------------ escritura
def _lock_chain(session: Session) -> None:
    """PostgreSQL: bloqueo consultivo de transacción; serializa a los escritores incluso con la tabla vacía."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": c.AUDIT_ADVISORY_LOCK_KEY})


def _check_detail(value: Any) -> None:
    """Solo str, int, bool, None, listas y dicts con claves str: así el JSON canónico es estable (sin floats ni fechas)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ErrorAuditoria(c.MSG_AUDIT_DETAIL_TYPE)
            _check_detail(item)
    elif isinstance(value, list):
        for item in value:
            _check_detail(item)
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise ErrorAuditoria(c.MSG_AUDIT_DETAIL_TYPE)


def _append_event(session: Session, accion: AccionAuditoria, actor: str, usuario_id: uuid.UUID | None,
                  recurso: str | None, detalle: dict, ip_hash: str | None, user_agent: str | None) -> EventoAuditoria:
    actor = clean_text(actor, c.DB_ACTOR_MAX) or c.ACTOR_ANONYMOUS
    recurso = clean_text(recurso, c.DB_RESOURCE_MAX)
    _check_detail(detalle)
    if len(canonical_json(detalle)) > c.AUDIT_DETAIL_MAX_BYTES:
        raise ErrorAuditoria(c.MSG_AUDIT_DETAIL_TOO_BIG.format(max=c.AUDIT_DETAIL_MAX_BYTES))
    signer, algorithm, fingerprint = _identity()
    for attempt in range(1, c.AUDIT_WRITE_RETRIES + 1):
        _lock_chain(session)
        head = session.execute(select(EventoAuditoria.hash_actual, EventoAuditoria.secuencia)
                               .order_by(EventoAuditoria.secuencia.desc()).limit(1)).first()
        prev, seq = (head.hash_actual, head.secuencia + 1) if head else (c.GENESIS_HASH, c.AUDIT_FIRST_SEQUENCE)
        ts = datetime.now(timezone.utc)
        current = chain_hash(prev, canonical_event(seq, ts, usuario_id, actor, accion, recurso, detalle, ip_hash,
                                                   user_agent, algorithm, fingerprint))
        signature = _sign(signer, event_signed_document(seq, prev, current, ts, usuario_id, accion, algorithm, fingerprint),
                          c.AUDIT_SIGN_CONTEXT)
        event = EventoAuditoria(secuencia=seq, ts_utc=ts, usuario_id=usuario_id, actor=actor, accion=accion, recurso=recurso,
                                detalle=detalle, ip_hash=ip_hash, user_agent=user_agent, hash_prev=prev, hash_actual=current,
                                firma_pqc=signature, algoritmo_firma=algorithm, huella_clave=fingerprint)
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
            # Ancla externa (solo en logs, con retención limitada): se emite al CONFIRMAR, nunca si hay rollback.
            sa_event.listen(session, "after_commit", lambda _s, s=seq, h=current: log.info(c.MSG_AUDIT_ANCHOR, s, h), once=True)
            return event
        except IntegrityError:   # red de seguridad: otro escritor tomó esa secuencia o ese hash_prev
            if attempt == c.AUDIT_WRITE_RETRIES:
                raise
    raise ErrorAuditoria(c.MSG_AUDIT_WRITE_FAILED)   # inalcanzable


def _run(work, db: Session | None, estricto: bool):
    """Ejecuta `work(session)` con la sesión dada o con una propia. Mejor esfuerzo salvo estricto o producción."""
    try:
        if db is not None:
            return work(db)
        if not db_session.db_enabled():
            if c.audit_requires_signature():       # producción sin base de datos: falla cerrado
                raise ErrorAuditoria(c.MSG_DB_DISABLED)
            return None
        with db_session.session_scope() as session:
            return work(session)
    except (SQLAlchemyError, ErrorAuditoria) as exc:
        if estricto or c.audit_requires_signature():
            raise exc if isinstance(exc, ErrorAuditoria) else ErrorAuditoria(c.MSG_AUDIT_WRITE_FAILED) from exc
        log.error("%s (%s)", c.MSG_AUDIT_WRITE_FAILED, type(exc).__name__)
        return None


# ------------------------------------------------------------------ API pública
@contextmanager
def unidad_de_trabajo() -> Iterator[Session | None]:
    """Sesión de BD con commit al salir (o None si la base no está configurada).

    Permite registrar la auditoría ANTES de escribir un archivo y deshacerla si el guardado falla.
    """
    if not db_session.db_enabled():
        if c.audit_requires_signature():
            raise ErrorAuditoria(c.MSG_DB_DISABLED)
        yield None
        return
    with db_session.session_scope() as session:
        yield session


def registrar_evento(accion: AccionAuditoria, actor: str, recurso: str | None = None, detalle: dict | None = None,
                     request: Any = None, usuario_id: uuid.UUID | None = None, db: Session | None = None,
                     estricto: bool = False) -> EventoAuditoria | None:
    ip_hash, user_agent = _request_context(request)
    return _run(lambda s: _append_event(s, AccionAuditoria(accion), actor, usuario_id, recurso, dict(detalle or {}),
                                        ip_hash, user_agent), db, estricto)


def registrar_login(usuario_id: uuid.UUID, actor: str, request: Any = None, detalle: dict | None = None,
                    db: Session | None = None) -> EventoAuditoria | None:
    return registrar_evento(AccionAuditoria.LOGIN, actor, recurso=str(usuario_id), detalle=detalle, request=request,
                            usuario_id=usuario_id, db=db)


def registrar_login_fallido(actor: str = c.ACTOR_ANONYMOUS, request: Any = None, motivo: str | None = None,
                            db: Session | None = None) -> EventoAuditoria | None:
    motivo = clean_text(motivo, c.DB_RESOURCE_MAX)
    return registrar_evento(AccionAuditoria.LOGIN_FALLIDO, actor, detalle={"motivo": motivo} if motivo else None,
                            request=request, db=db)


@dataclass
class RegistroCarga:
    evento: EventoAuditoria
    carga: CargaArchivo | None            # None si el contenido se cortó: no hay hash que guardar


def registrar_carga(tipo: str, nombre_archivo: str, contenido: bytes, filas: int, valido: bool, total_errores: int,
                    accion: AccionAuditoria, actor: str = c.ACTOR_API_KEY, request: Any = None,
                    ruta_almacenamiento: str | None = None, ruta_historial: str | None = None,
                    usuario_id: uuid.UUID | None = None, db: Session | None = None,
                    truncado: bool = False) -> RegistroCarga | None:
    """Guarda la fila de cargas_archivo (SHA3-256 y firma del archivo) y el evento encadenado.

    `truncado=True`: el servidor dejó de leer el archivo al pasar el límite de tamaño, así que `contenido` es solo
    el principio. El evento registra los bytes leídos, `sha3_256: null` y `truncado: true` (un hash del principio no
    es el del archivo), y no se crea fila en cargas_archivo, que exige el hash.

    Con `db` no hace commit: el llamador decide (así puede registrar antes de escribir el archivo y
    deshacerlo todo si el guardado falla).
    """
    digest = None if truncado else hashlib.sha3_256(contenido).hexdigest()
    ip_hash, user_agent = _request_context(request)
    nombre_archivo = clean_text(nombre_archivo, c.DB_FILENAME_MAX) or ""
    actor = clean_text(actor, c.DB_ACTOR_MAX) or c.ACTOR_ANONYMOUS

    def work(session: Session) -> RegistroCarga:
        carga = None
        if not truncado:
            signer, algorithm, fingerprint = _identity()
            signature = _sign(signer, {"v": c.SIGNED_DOC_VERSION, "sha3_256": digest, "nombre_archivo": nombre_archivo,
                                       "tipo": tipo, "tamano_bytes": len(contenido), "algoritmo_firma": algorithm,
                                       "huella_clave": fingerprint}, c.UPLOAD_SIGN_CONTEXT)
            carga = CargaArchivo(usuario_id=usuario_id, actor=actor, tipo=TipoCarga(tipo), nombre_archivo=nombre_archivo,
                                 sha3_256=digest, tamano_bytes=len(contenido), filas=filas, valido=valido,
                                 total_errores=total_errores, ruta_almacenamiento=ruta_almacenamiento,
                                 ruta_historial=ruta_historial, firma_pqc=signature, algoritmo_firma=algorithm,
                                 huella_clave=fingerprint)
            session.add(carga)
        detalle = {"tipo": tipo, "sha3_256": digest, "tamano_bytes": len(contenido), "filas": filas, "valido": valido,
                   "total_errores": total_errores}
        if truncado:
            detalle[c.KEY_TRUNCADO] = True
        event = _append_event(session, AccionAuditoria(accion), actor, usuario_id, nombre_archivo, detalle, ip_hash, user_agent)
        return RegistroCarga(event, carga)

    return _run(work, db, estricto=False)


def consultar(usuario: uuid.UUID | str | None = None, desde: datetime | None = None, hasta: datetime | None = None,
              accion: AccionAuditoria | None = None, limite: int = c.AUDIT_PAGE_DEFAULT, offset: int = 0,
              db: Session | None = None) -> tuple[list[EventoAuditoria], int]:
    """Eventos más recientes primero. `usuario` puede ser un UUID (usuario_id) o un texto (actor)."""
    limite = max(1, min(limite, c.AUDIT_PAGE_MAX))

    def work(session: Session):
        conds = []
        if isinstance(usuario, uuid.UUID):
            conds.append(EventoAuditoria.usuario_id == usuario)
        elif usuario:
            conds.append(EventoAuditoria.actor == usuario)
        if desde:
            conds.append(EventoAuditoria.ts_utc >= desde)
        if hasta:
            conds.append(EventoAuditoria.ts_utc <= hasta)
        if accion:
            conds.append(EventoAuditoria.accion == AccionAuditoria(accion))
        total = session.execute(select(func.count()).select_from(EventoAuditoria).where(*conds)).scalar_one()
        rows = session.execute(select(EventoAuditoria).where(*conds).order_by(EventoAuditoria.secuencia.desc())
                               .limit(limite).offset(max(0, offset))).scalars().all()
        return list(rows), total

    return _run(work, db, estricto=True)


@dataclass
class ResultadoVerificacion:
    valida: bool
    n_eventos: int
    hash_cabeza: str | None = None             # hash_actual del último evento verificado (compáralo con el ancla)
    ultima_secuencia: int = 0
    primer_hash_prev_es_genesis: bool | None = None
    error_secuencia: int | None = None
    motivo: str | None = None


def verificar_cadena(db: Session | None = None) -> ResultadoVerificacion:
    """Recorre toda la cadena: génesis, secuencia, enlaces, hashes recalculados y firmas. Se detiene en el primer fallo.

    Un evento sin firma (algoritmo NINGUNO) NO es válido. Limitación: borrar la cola de la cadena o vaciarla y empezar
    de nuevo no se detecta sin un ancla externa; compara `hash_cabeza` y `ultima_secuencia` con el último registro
    "auditoria cabeza" del log estructurado. En PostgreSQL un trigger impide UPDATE, DELETE y TRUNCATE.
    """
    def work(session: Session) -> ResultadoVerificacion:
        signer = get_firmante()
        current_fingerprint = None
        try:
            current_fingerprint = signer.huella_clave_publica() if signer is not None else None
        except ErrorClavesPQC:
            signer = None
        expected_prev, expected_seq, total, first_ok = c.GENESIS_HASH, c.AUDIT_FIRST_SEQUENCE, 0, None
        stream = session.execute(select(EventoAuditoria).order_by(EventoAuditoria.secuencia)
                                 .execution_options(yield_per=c.AUDIT_VERIFY_BATCH)).scalars()
        head, last_seq = None, 0
        for ev in stream:
            total += 1
            if first_ok is None:
                first_ok = ev.hash_prev == c.GENESIS_HASH

            def fail(reason: str) -> ResultadoVerificacion:
                return ResultadoVerificacion(False, total, head, last_seq, first_ok, ev.secuencia, reason)

            if ev.secuencia != expected_seq:
                return fail(c.MSG_AUDIT_BAD_SEQUENCE)
            if ev.hash_prev != expected_prev:
                return fail(c.MSG_AUDIT_BROKEN_LINK)
            canonical = canonical_event(ev.secuencia, ev.ts_utc, ev.usuario_id, ev.actor, ev.accion, ev.recurso,
                                        ev.detalle or {}, ev.ip_hash, ev.user_agent, ev.algoritmo_firma, ev.huella_clave)
            if chain_hash(ev.hash_prev, canonical) != ev.hash_actual:
                return fail(c.MSG_AUDIT_BAD_HASH)
            if ev.firma_pqc is None or ev.algoritmo_firma == c.UNSIGNED_ALGORITHM:
                return fail(c.MSG_AUDIT_UNSIGNED)
            if signer is None:
                return fail(c.MSG_AUDIT_CANNOT_VERIFY)
            if ev.algoritmo_firma != signer.ALGORITMO:
                return fail(c.MSG_AUDIT_ALGORITHM_MISMATCH)
            if ev.huella_clave != current_fingerprint:
                return fail(c.MSG_AUDIT_KEY_MISMATCH)
            document = event_signed_document(ev.secuencia, ev.hash_prev, ev.hash_actual, ev.ts_utc, ev.usuario_id, ev.accion,
                                             ev.algoritmo_firma, ev.huella_clave)
            if not signer.verificar(canonical_json(document), ev.firma_pqc, contexto=c.AUDIT_SIGN_CONTEXT):
                return fail(c.MSG_AUDIT_BAD_SIGNATURE)
            expected_prev, expected_seq, head, last_seq = ev.hash_actual, ev.secuencia + 1, ev.hash_actual, ev.secuencia
        return ResultadoVerificacion(True, total, head, last_seq, first_ok)

    return _run(work, db, estricto=True)

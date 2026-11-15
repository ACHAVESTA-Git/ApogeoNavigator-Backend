import base64
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.enums import AccionAuditoria
from app.schemas.common import Page


class EventoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    secuencia: int
    ts_utc: datetime
    usuario_id: uuid.UUID | None
    actor: str
    accion: AccionAuditoria
    recurso: str | None
    detalle: dict
    ip_hash: str | None
    user_agent: str | None
    hash_prev: str
    hash_actual: str
    algoritmo_firma: str | None
    huella_clave: str | None
    firma_b64: str | None = None


def evento_out(evento, incluir_firma: bool = False) -> EventoOut:
    out = EventoOut.model_validate(evento)
    if incluir_firma and evento.firma_pqc:
        out.firma_b64 = base64.b64encode(evento.firma_pqc).decode("ascii")
    return out


PaginaAuditoria = Page[EventoOut]


class VerificacionOut(BaseModel):
    valida: bool
    n_eventos: int
    hash_cabeza: str | None
    ultima_secuencia: int
    primer_hash_prev_es_genesis: bool | None
    error_secuencia: int | None
    motivo: str | None

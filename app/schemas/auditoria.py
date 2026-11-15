import base64
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.enums import AccionAuditoria
from app.schemas.common import Page


class EventoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, json_schema_extra={"examples": [{
        "id": 1, "secuencia": 1, "ts_utc": "2026-11-14T21:30:00.123456Z", "usuario_id": None, "actor": "api-key",
        "accion": "CARGA_GUARDADA", "recurso": "sites.csv",
        "detalle": {"tipo": "sites", "filas": 10, "valido": True, "total_errores": 0},
        "ip_hash": None, "user_agent": "curl/8.7.1", "hash_prev": "0000000000000000000000000000000000000000000000000000000000000000", "hash_actual": "0852331cd5ef932d7efbba8912e03bbaba4f5dba994ef5d7789c1bbab791a2f8",
        "algoritmo_firma": "ML-DSA-65", "huella_clave": "38de7c896605b5b29b45c640340ce80f5325979cbde219e0434868f70c198813", "firma_b64": None}]})

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

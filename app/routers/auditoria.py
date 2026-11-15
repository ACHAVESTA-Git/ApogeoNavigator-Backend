"""Consulta y verificación de la auditoría. Protegido con X-API-Key hasta que exista OAuth."""
import uuid
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core import config
from app.core.security import require_api_key
from app.db.enums import AccionAuditoria
from app.db.session import get_db
from app.schemas.auditoria import PaginaAuditoria, VerificacionOut, evento_out
from app.utils import auditoria_util
from app.utils.dates import as_utc

router = APIRouter(tags=[config.ROUTER_TAG_AUDIT], dependencies=[Depends(require_api_key)])


@router.get(config.ROUTE_AUDIT, response_model=PaginaAuditoria)
def listar(usuario: str | None = Query(None, description="UUID del usuario o texto del actor"),
           accion: AccionAuditoria | None = None, desde: datetime | None = None, hasta: datetime | None = None,
           limite: int = Query(config.AUDIT_PAGE_DEFAULT, ge=1, le=config.AUDIT_PAGE_MAX), offset: int = Query(0, ge=0),
           incluir_firma: bool = False, db: Session = Depends(get_db)):
    desde, hasta = (as_utc(d) if d else None for d in (desde, hasta))
    if desde and hasta and hasta <= desde:
        raise HTTPException(status_code=config.HTTP_UNPROCESSABLE, detail=config.MSG_AUDIT_RANGE_ORDER)
    try:
        quien: uuid.UUID | str | None = uuid.UUID(usuario) if usuario else None
    except ValueError:
        quien = usuario
    items, total = auditoria_util.consultar(quien, desde, hasta, accion, limite, offset, db=db)
    return PaginaAuditoria(total=total, limite=limite, offset=offset,
                           items=[evento_out(e, incluir_firma) for e in items])


@router.get(config.ROUTE_AUDIT_VERIFY, response_model=VerificacionOut)
def verificar(db: Session = Depends(get_db)):
    """Recorre toda la cadena. Compara `hash_cabeza` con el último «auditoria cabeza» del log para detectar truncados."""
    return VerificacionOut(**asdict(auditoria_util.verificar_cadena(db=db)))

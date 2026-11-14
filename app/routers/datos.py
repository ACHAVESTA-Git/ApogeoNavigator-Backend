"""Carga de CSV: dry-run (validar) y guardado atómico. Protegido con X-API-Key."""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.core import config
from app.core.security import require_api_key
from app.db.enums import AccionAuditoria
from app.schemas.report import Report, SaveResult
from app.security.pqc import ErrorClavesPQC
from app.services import storage
from app.utils import auditoria_util
from app.utils.csv_reader import read_limited
from app.validation.engine import ValidationResult, validate_csv

router = APIRouter(prefix=config.UPLOAD_ROUTER_PREFIX, tags=[config.ROUTER_TAG], dependencies=[Depends(require_api_key)])

RESPONSES = {
    401: {"description": "API key inválida o ausente"},
    413: {"description": "E_TAMANO"},
    415: {"description": "E_TIPO_ARCHIVO"},
    422: {"description": "Informe con los errores de validación (detail)"},
}


def _check_tipo(tipo: str) -> None:
    if tipo not in config.TIPOS_ARCHIVO:
        raise HTTPException(status_code=config.HTTP_NOT_FOUND, detail=config.MSG_UNKNOWN_TIPO.format(tipos=list(config.TIPOS_ARCHIVO)))


def _validate(tipo: str, file: UploadFile) -> ValidationResult:
    _check_tipo(tipo)
    raw = read_limited(file.file)
    return validate_csv(tipo, file.filename, raw, storage.build_context())


def _audit(tipo: str, result: ValidationResult, raw_size_source: bytes, accion: AccionAuditoria, request: Request) -> None:
    """Registra la carga (validada o rechazada). En producción, si no se puede firmar: 503."""
    try:
        auditoria_util.registrar_carga(tipo, result.report.archivo, raw_size_source, result.report.total_filas,
                                       result.report.valido, result.report.total_errores, accion, request=request)
    except auditoria_util.ErrorAuditoria:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_AUDIT_UNAVAILABLE) from None


def _fail(result: ValidationResult) -> JSONResponse:
    return JSONResponse(status_code=result.http_status, content={"detail": result.report.model_dump()})


@router.post("/validar/{tipo}", response_model=Report, responses=RESPONSES)
def validar(tipo: str, file: UploadFile, request: Request):
    """Dry-run: valida y devuelve el informe sin guardar nada. Registra la validación en la auditoría."""
    result = _validate(tipo, file)
    ok = result.report.valido
    _audit(tipo, result, result.content, AccionAuditoria.CARGA_VALIDADA if ok else AccionAuditoria.CARGA_RECHAZADA, request)
    return result.report if ok else _fail(result)


@router.post("/{tipo}", response_model=SaveResult, responses=RESPONSES)
def guardar(tipo: str, file: UploadFile, request: Request):
    """Valida y, si no hay errores, guarda de forma atómica con copia en data/_historial/.

    La auditoría se registra ANTES de escribir y se confirma después: si firmar el manifiesto o el evento
    falla, no se guarda nada y la respuesta es 503.
    """
    result = _validate(tipo, file)
    if not result.report.valido:
        _audit(tipo, result, result.content, AccionAuditoria.CARGA_RECHAZADA, request)
        return _fail(result)
    try:
        with auditoria_util.unidad_de_trabajo() as db:
            registro = auditoria_util.registrar_carga(
                tipo, result.target_filename, result.content, result.report.total_filas, True, 0,
                AccionAuditoria.CARGA_GUARDADA, request=request, ruta_almacenamiento=result.target_filename, db=db) if db else None
            backup = storage.save_atomic(result.target_filename, result.content)
            if registro and backup:
                registro.carga.ruta_historial = backup.name
    except auditoria_util.ErrorAuditoria:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_AUDIT_UNAVAILABLE) from None
    except ErrorClavesPQC:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_SIGNING_UNAVAILABLE) from None
    return SaveResult(**result.report.model_dump(), guardado=True,
                      copia_historial=backup.name if backup else None)

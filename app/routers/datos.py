"""Carga de CSV: dry-run (validar) y guardado atómico. Protegido con X-API-Key."""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.core import config
from app.core.respuestas import respuesta_error
from app.core.security import require_api_key
from app.db.enums import AccionAuditoria
from app.schemas.report import Report, SaveResult
from app.security.pqc import ErrorClavesPQC
from app.services import datastore, storage
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


def _validate(tipo: str, file: UploadFile) -> tuple[ValidationResult, bytes]:
    """Valida y devuelve también los bytes leídos (a lo sumo MAX_FILE_BYTES + 1) para poder auditar los rechazos."""
    _check_tipo(tipo)
    raw = read_limited(file.file)
    return validate_csv(tipo, file.filename, raw, storage.build_context()), raw


def _audit(tipo: str, result: ValidationResult, raw: bytes, accion: AccionAuditoria, request: Request) -> None:
    """Registra la carga (validada o rechazada). En producción, si no se puede firmar: 503.

    Si el archivo falló antes de leerse entero (tamaño, binario, codificación, nombre...), `result.content` va vacío:
    se audita lo realmente leído. Si se cortó por superar el límite, no se calcula el hash (ver `registrar_carga`).
    """
    try:
        auditoria_util.registrar_carga(tipo, result.report.archivo, result.content or raw, result.report.total_filas,
                                       result.report.valido, result.report.total_errores, accion, request=request,
                                       truncado=len(raw) > config.MAX_FILE_BYTES)
    except auditoria_util.ErrorAuditoria:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_AUDIT_UNAVAILABLE) from None


def _fail(result: ValidationResult, request: Request) -> JSONResponse:
    informe = result.report
    return respuesta_error(request, result.http_status, informe.model_dump(), codigo=config.CODE_CSV_INVALID,
                           resumen=config.MSG_CSV_SUMMARY.format(n=informe.total_errores))


@router.post("/validar/{tipo}", response_model=Report, responses=RESPONSES)
def validar(tipo: str, file: UploadFile, request: Request):
    """Dry-run: valida y devuelve el informe sin guardar nada. Registra la validación en la auditoría."""
    result, raw = _validate(tipo, file)
    ok = result.report.valido
    _audit(tipo, result, raw, AccionAuditoria.CARGA_VALIDADA if ok else AccionAuditoria.CARGA_RECHAZADA, request)
    return result.report if ok else _fail(result, request)


@router.post("/{tipo}", response_model=SaveResult, responses=RESPONSES)
def guardar(tipo: str, file: UploadFile, request: Request):
    """Valida y, si no hay errores, guarda de forma atómica con copia en data/_historial/.

    La auditoría se registra ANTES de escribir y se confirma después: si firmar el manifiesto o el evento
    falla, no se guarda nada y la respuesta es 503.
    """
    result, raw = _validate(tipo, file)
    if not result.report.valido:
        _audit(tipo, result, raw, AccionAuditoria.CARGA_RECHAZADA, request)
        return _fail(result, request)
    try:
        with auditoria_util.unidad_de_trabajo() as db:
            registro = auditoria_util.registrar_carga(
                tipo, result.target_filename, result.content, result.report.total_filas, True, 0,
                AccionAuditoria.CARGA_GUARDADA, request=request, ruta_almacenamiento=result.target_filename, db=db) if db else None
            backup = datastore.guardar(result.target_filename, result.content)
            if registro and backup:
                registro.carga.ruta_historial = backup.name
    except auditoria_util.ErrorAuditoria:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_AUDIT_UNAVAILABLE) from None
    except ErrorClavesPQC:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_SIGNING_UNAVAILABLE) from None
    except datastore.DataConflict:
        raise HTTPException(status_code=config.HTTP_CONFLICT, detail=config.MSG_DATA_CONFLICT) from None
    return SaveResult(**result.report.model_dump(), guardado=True,
                      copia_historial=backup.name if backup else None)

"""Carga de CSV: dry-run (validar) y guardado atómico. Protegido con X-API-Key."""
from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import JSONResponse

from app.api.deps import get_carga_service
from app.core import config
from app.core.respuestas import respuesta_error
from app.core.security import require_api_key
from app.db.enums import AccionAuditoria
from app.schemas.common import ErrorOut
from app.schemas.report import ErrorValidacionOut, Report, SaveResult
from app.services.carga import CargaService
from app.validation.engine import ValidationResult

router = APIRouter(prefix=config.UPLOAD_ROUTER_PREFIX, tags=[config.ROUTER_TAG], dependencies=[Depends(require_api_key)])

RESPONSES = {
    401: {"description": "Missing or invalid API key", "model": ErrorOut},
    413: {"description": "E_TAMANO: file over 5 MB or 10 000 rows; the body also carries `codigo`", "model": ErrorOut},
    415: {"description": "E_TIPO_ARCHIVO: not a .csv text file; `detail` carries the report", "model": ErrorValidacionOut},
    422: {"description": "Rejected file (`detail` is the validation report) or malformed request (`detail` is FastAPI's list of errors)", "model": ErrorValidacionOut},
}


def _resumen(n: int) -> str:
    return config.MSG_CSV_SUMMARY_ONE if n == 1 else config.MSG_CSV_SUMMARY_MANY.format(n=n)


def _fail(result: ValidationResult, request: Request) -> JSONResponse:
    informe = result.report
    return respuesta_error(request, result.http_status, informe.model_dump(), codigo=config.CODE_CSV_INVALID,
                           resumen=_resumen(informe.total_errores))


@router.post("/validar/{tipo}", response_model=Report, responses=RESPONSES, summary="Validate a CSV file without saving it (dry run)")
def validar(tipo: str, file: UploadFile, request: Request, servicio: CargaService = Depends(get_carga_service)):
    """Dry run: validates and returns the report without saving anything. The validation is recorded in the audit log."""
    revision = servicio.revisar(tipo, file.filename, file.file)
    ok = revision.resultado.report.valido
    servicio.auditar(tipo, revision, AccionAuditoria.CARGA_VALIDADA if ok else AccionAuditoria.CARGA_RECHAZADA, request)
    return revision.resultado.report if ok else _fail(revision.resultado, request)


@router.post("/{tipo}", response_model=SaveResult, responses=RESPONSES, summary="Validate and save a CSV file")
def guardar(tipo: str, file: UploadFile, request: Request, servicio: CargaService = Depends(get_carga_service)):
    """Validates and, if there are no errors, saves atomically, keeping the previous version in data/_historial/.

    The audit event is written BEFORE saving and committed afterwards: if signing the manifest or the event fails,
    nothing is saved and the response is 503.
    """
    revision = servicio.revisar(tipo, file.filename, file.file)
    if not revision.resultado.report.valido:
        servicio.auditar(tipo, revision, AccionAuditoria.CARGA_RECHAZADA, request)
        return _fail(revision.resultado, request)
    return servicio.guardar_carga(tipo, revision, request)

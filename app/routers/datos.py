"""Carga de CSV: dry-run (validar) y guardado atómico. Protegido con X-API-Key."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.core import config
from app.core.security import require_api_key
from app.schemas.report import Report, SaveResult
from app.services import storage
from app.utils.csv_reader import read_limited
from app.validation.engine import ValidationResult, validate_csv

router = APIRouter(prefix="/api/datos", tags=["datos"], dependencies=[Depends(require_api_key)])

RESPONSES = {
    401: {"description": "API key inválida o ausente"},
    413: {"description": "E_TAMANO"},
    415: {"description": "E_TIPO_ARCHIVO"},
    422: {"description": "Informe con los errores de validación (detail)"},
}


def _check_tipo(tipo: str) -> None:
    if tipo not in config.TIPOS_ARCHIVO:
        raise HTTPException(status_code=404, detail=f"Tipo desconocido; usa uno de {list(config.TIPOS_ARCHIVO)}")


async def _validate(tipo: str, file: UploadFile) -> ValidationResult:
    _check_tipo(tipo)
    raw = read_limited(file.file)
    return validate_csv(tipo, file.filename, raw, storage.build_context())


def _fail(result: ValidationResult) -> JSONResponse:
    return JSONResponse(status_code=result.http_status, content={"detail": result.report.model_dump()})


@router.post("/validar/{tipo}", response_model=Report, responses=RESPONSES)
async def validar(tipo: str, file: UploadFile):
    """Dry-run: valida y devuelve el informe sin guardar nada."""
    result = await _validate(tipo, file)
    return result.report if result.report.valido else _fail(result)


@router.post("/{tipo}", response_model=SaveResult, responses=RESPONSES)
async def guardar(tipo: str, file: UploadFile):
    """Valida y, si no hay errores, guarda de forma atómica con copia en data/_historial/."""
    result = await _validate(tipo, file)
    if not result.report.valido:
        return _fail(result)
    backup = storage.save_atomic(result.report.archivo, result.content)
    return SaveResult(**result.report.model_dump(), guardado=True,
                      copia_historial=backup.name if backup else None)

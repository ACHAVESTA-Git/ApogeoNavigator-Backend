"""Piezas comunes de los routers de consulta: descripciones de error documentadas en OpenAPI."""
from app.core import config as c
from app.schemas.common import ErrorOut

RESPONSES = {
    c.HTTP_NOT_FOUND: {"description": c.DESC_NOT_FOUND, "model": ErrorOut},
    c.HTTP_UNPROCESSABLE: {"description": c.DESC_UNPROCESSABLE, "model": ErrorOut},
    c.HTTP_UNAVAILABLE: {"description": c.DESC_UNAVAILABLE, "model": ErrorOut},
}

"""Fecha UTC con formato YYYY-MM-DDTHH:MMZ."""
from datetime import datetime, timezone

from app.core import config
from app.utils.errors import CellError


def parse_utc(value: str) -> datetime:
    if not config.UTC_RE.fullmatch(value):
        raise CellError(config.E_FORMATO, "Formato esperado: YYYY-MM-DDTHH:MMZ (UTC)")
    try:
        return datetime.strptime(value, config.UTC_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise CellError(config.E_TIPO, "No es una fecha válida") from None

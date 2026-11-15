"""Fecha UTC con formato YYYY-MM-DDTHH:MMZ."""
from datetime import datetime, timezone

from app.core import config
from app.utils.errors import CellError


def as_utc(value: datetime) -> datetime:
    """Fecha con zona UTC: una fecha sin zona se interpreta como UTC; una con zona se convierte a UTC.

    Evita el TypeError de comparar fechas con y sin zona horaria.
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_utc(value: str) -> datetime:
    if not config.UTC_RE.fullmatch(value):
        raise CellError(config.E_FORMATO, config.MSG_DATE_FORMAT)
    try:
        return datetime.strptime(value, config.UTC_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise CellError(config.E_TIPO, config.MSG_DATE_INVALID) from None

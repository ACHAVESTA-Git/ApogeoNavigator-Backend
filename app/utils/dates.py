"""Fecha UTC con formato YYYY-MM-DDTHH:MMZ."""
from datetime import datetime, timezone

from app.core import config
from app.utils.errors import CellError


def parse_utc(value: str) -> datetime:
    if not config.UTC_RE.fullmatch(value):
        raise CellError(config.E_FORMATO, config.MSG_DATE_FORMAT)
    try:
        return datetime.strptime(value, config.UTC_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise CellError(config.E_TIPO, config.MSG_DATE_INVALID) from None

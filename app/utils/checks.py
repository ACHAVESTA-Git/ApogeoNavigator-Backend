"""Validadores de enum, regex y URL https."""
import re
from typing import Sequence

from app.core import config
from app.utils.errors import CellError


def validate_enum(value: str, choices: Sequence[str]) -> str:
    if value not in choices:
        raise CellError(config.E_ENUM, "Valores permitidos: " + ", ".join(choices))
    return value


def validate_regex(value: str, pattern: re.Pattern) -> str:
    if not pattern.match(value):
        raise CellError(config.E_FORMATO, f"No cumple el formato {pattern.pattern}")
    return value


def validate_https_url(value: str) -> str:
    return validate_regex(value, config.HTTPS_URL_RE)

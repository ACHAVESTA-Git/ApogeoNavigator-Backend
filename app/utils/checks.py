"""Validadores de enum, regex y URL https."""
import re
from typing import Sequence

from app.core import config
from app.utils.errors import CellError


def validate_enum(value: str, choices: Sequence[str]) -> str:
    if value not in choices:
        raise CellError(config.E_ENUM, config.MSG_ENUM.format(values=", ".join(choices)))
    return value


def validate_regex(value: str, pattern: re.Pattern) -> str:
    if not pattern.fullmatch(value):
        raise CellError(config.E_FORMATO, config.MSG_REGEX.format(pattern=pattern.pattern))
    return value


def validate_https_url(value: str) -> str:
    return validate_regex(value, config.HTTPS_URL_RE)

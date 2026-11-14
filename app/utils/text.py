"""Validadores de texto reutilizables: longitud, caracteres prohibidos, fórmulas y recortes."""
from app.core import config
from app.utils.errors import CellError


def check_safe_chars(value: str, allow_formula: bool = False) -> None:
    """Rechaza caracteres de control, `<` `>` y posibles inyecciones de fórmula."""
    for ch in value:
        if ord(ch) <= config.CONTROL_CHAR_MAX and ch not in config.ALLOWED_CONTROL_CHARS:
            raise CellError(config.E_CARACTER, config.MSG_CONTROL_CHAR)
        if ch in config.FORBIDDEN_TEXT_CHARS:
            raise CellError(config.E_CARACTER, config.MSG_FORBIDDEN_CHAR.format(char=ch))
    if not allow_formula and value.startswith(config.FORMULA_PREFIXES):
        raise CellError(config.E_CARACTER, config.MSG_FORMULA)


def check_length(value: str, min_len: int, max_len: int) -> None:
    if not min_len <= len(value) <= max_len:
        raise CellError(config.E_LONGITUD, config.MSG_LENGTH.format(lo=min_len, hi=max_len))


def validate_text(value: str, min_len: int, max_len: int, allow_formula: bool = False) -> str:
    check_safe_chars(value, allow_formula)
    check_length(value, min_len, max_len)
    return value


def check_not_null_token(value: str) -> None:
    """Rechaza nulos disfrazados (NULL, NaN, N/A, -): el nulo es el campo vacío."""
    if value.upper() in config.NULL_TOKENS:
        raise CellError(config.E_FORMATO, config.MSG_NULL_TOKEN)


def clip_value(value, max_chars: int = config.MAX_VALUE_CHARS) -> str | None:
    """Recorta un valor del usuario para el informe."""
    return None if value is None else str(value)[:max_chars]


def clean_text(value: str | None, max_chars: int) -> str | None:
    """Texto del usuario apto para guardar: sin caracteres de control y recortado a `max_chars`."""
    if value is None:
        return None
    return "".join(ch for ch in str(value) if ord(ch) > config.CONTROL_CHAR_MAX)[:max_chars]


def sanitize_filename(name: str | None) -> str:
    """Nombre apto para el informe: sin caracteres de control y con longitud limitada."""
    cleaned = "".join(ch for ch in (name or "") if ord(ch) > config.CONTROL_CHAR_MAX)
    return cleaned[: config.MAX_FILENAME_CHARS]

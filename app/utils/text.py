"""Validadores de texto reutilizables: longitud, caracteres prohibidos, fórmulas."""
from app.core import config
from app.utils.errors import CellError


def check_safe_chars(value: str, allow_formula: bool = False) -> None:
    """Rechaza caracteres de control, `<` `>` y posibles inyecciones de fórmula."""
    for ch in value:
        if ord(ch) <= config.CONTROL_CHAR_MAX and ch not in config.ALLOWED_CONTROL_CHARS:
            raise CellError(config.E_CARACTER, "Contiene un caracter de control")
        if ch in config.FORBIDDEN_TEXT_CHARS:
            raise CellError(config.E_CARACTER, f"Caracter no permitido: {ch}")
    if not allow_formula and value.startswith(config.FORMULA_PREFIXES):
        raise CellError(config.E_CARACTER, "No puede empezar por = + - @ (posible fórmula)")


def check_length(value: str, min_len: int, max_len: int) -> None:
    if not min_len <= len(value) <= max_len:
        raise CellError(config.E_LONGITUD, f"Debe tener entre {min_len} y {max_len} caracteres")


def validate_text(value: str, min_len: int, max_len: int, allow_formula: bool = False) -> str:
    check_safe_chars(value, allow_formula)
    check_length(value, min_len, max_len)
    return value

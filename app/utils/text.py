"""Validadores de texto reutilizables: longitud, caracteres prohibidos, fórmulas y recortes."""
import unicodedata

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


def es_peligroso(ch: str) -> bool:
    """Caracteres que no deben llegar a la base, a un nombre de archivo ni a una línea de log: controles ASCII y C1 (incluidos
    CR, LF, tab y DEL), de formato (marcas bidireccionales, ancho cero) y los separadores de línea y párrafo U+2028 y U+2029."""
    return unicodedata.category(ch) in config.UNSAFE_CHAR_CATEGORIES


def clean_text(value: str | None, max_chars: int) -> str | None:
    """Texto del usuario apto para guardar: sin caracteres peligrosos (ver `es_peligroso`) y recortado a `max_chars`."""
    if value is None:
        return None
    return "".join(ch for ch in str(value) if not es_peligroso(ch))[:max_chars]


def escapar_control(value, max_chars: int | None = None) -> str:
    """Texto para una línea de log: los caracteres peligrosos se sustituyen por su escape visible (\\r, \\n, \\x85, \\u2028…),
    de modo que un cliente no pueda fabricar líneas falsas. Se recorta a `max_chars` si se indica."""
    partes = []
    for ch in str(value):
        if not es_peligroso(ch):
            partes.append(ch)
        elif ch in config.LOG_ESCAPES:
            partes.append(config.LOG_ESCAPES[ch])
        else:
            partes.append(f"\\x{ord(ch):02x}" if ord(ch) <= 0xFF else f"\\u{ord(ch):04x}")
    texto = "".join(partes)
    return texto if max_chars is None or len(texto) <= max_chars else texto[:max_chars] + "…"


def sanitize_filename(name: str | None) -> str:
    """Nombre apto para el informe: sin caracteres peligrosos y con longitud limitada."""
    return "".join(ch for ch in (name or "") if not es_peligroso(ch))[: config.MAX_FILENAME_CHARS]

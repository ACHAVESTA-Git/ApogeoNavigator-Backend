"""Validador de decimales: tipo, finito, decimales y rango con signo."""
from decimal import Decimal

from app.core import config
from app.utils.errors import CellError


def validate_decimal(
    value: str,
    lo: float,
    hi: float,
    hi_inclusive: bool = True,
    max_decimals: int = config.MAX_DECIMALS,
) -> float:
    """Convierte `value` a float. `-0` se normaliza a 0.0."""
    if not config.DECIMAL_RE.fullmatch(value):
        raise CellError(config.E_TIPO, "No es un número decimal (sin notación científica, inf ni nan)")
    decimals = len(value.split(".")[1]) if "." in value else 0
    if decimals > max_decimals:
        raise CellError(config.E_DECIMALES, f"Máximo {max_decimals} decimales")
    number = float(Decimal(value))
    upper_ok = number <= hi if hi_inclusive else number < hi
    if not (lo <= number and upper_ok):
        bound = f"{hi:g}" if hi_inclusive else f"{hi:g} (excluido)"
        raise CellError(config.E_RANGO, f"Debe estar entre {lo:g} y {bound}")
    return number + 0.0

"""Validador de decimales: tipo, finito, decimales y rango con signo."""
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
        raise CellError(config.E_TIPO, config.MSG_NOT_NUMBER)
    decimals = len(value.split(".")[1]) if "." in value else 0
    if decimals > max_decimals:
        raise CellError(config.E_DECIMALES, config.MSG_DECIMALS.format(max=max_decimals))
    number = float(value)
    upper_ok = number <= hi if hi_inclusive else number < hi
    if not (lo <= number and upper_ok):
        template = config.MSG_RANGE if hi_inclusive else config.MSG_RANGE_EXCLUDED
        raise CellError(config.E_RANGO, template.format(lo=lo, hi=hi))
    return number + 0.0

"""Esquemas declarativos por tipo de archivo (ver docs/plan/estandar-csv.md §2-§4)."""
from dataclasses import dataclass
from typing import Any

from app.core import config as c

TEXT, DECIMAL, DATE, ENUM, REGEX, URL = "text", "decimal", "date", "enum", "regex", "url"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    kind: str
    required: bool = True
    min_len: int = 0
    max_len: int = 0
    lo: float = 0.0
    hi: float = 0.0
    hi_inclusive: bool = True
    choices: tuple[str, ...] = ()
    pattern: Any = None
    unique: bool = False
    default: Any = None          # valor cuando una columna opcional llega vacía


def _dec(name, lo, hi, required=True, hi_inclusive=True, default=None) -> ColumnSpec:
    return ColumnSpec(name, DECIMAL, required, lo=lo, hi=hi, hi_inclusive=hi_inclusive, default=default)


def _text(name, min_len, max_len, required=True) -> ColumnSpec:
    return ColumnSpec(name, TEXT, required, min_len=min_len, max_len=max_len)


def _date(name) -> ColumnSpec:
    return ColumnSpec(name, DATE)


SITES_SCHEMA = [
    ColumnSpec("site_id", REGEX, pattern=c.SITE_ID_RE, unique=True),
    _text("nombre", 3, 120),
    ColumnSpec("categoria", ENUM, choices=c.SITE_CATEGORIAS),
    _text("mision", 2, 80),
    _text("agencia", 2, 40),
    _dec("lat_deg", c.LAT_MIN, c.LAT_MAX),
    _dec("lon_este_deg", c.LON_MIN, c.LON_MAX),
    _dec("alt_km", c.ALT_KM_MIN, c.ALT_KM_MAX, required=False, default=0.0),
    ColumnSpec("fuente_url", URL, min_len=10, max_len=300),
    _text("notas", 0, 500, required=False),
]

EFEMERIDES_SCHEMA = [
    _date("utc"),
    _dec("sol_az_deg", c.AZ_MIN, c.AZ_MAX, hi_inclusive=False),
    _dec("sol_el_deg", c.EL_MIN, c.EL_MAX),
    _dec("tierra_az_deg", c.AZ_MIN, c.AZ_MAX, hi_inclusive=False),
    _dec("tierra_el_deg", c.EL_MIN, c.EL_MAX),
]

# Las horas se acotan contra el rango real en la coherencia; aquí solo el mínimo.
_HOURS_CAP = c.METRICS_MAX_RANGE_DAYS * 24.0

METRICAS_SCHEMA = [
    ColumnSpec("site_id", REGEX, pattern=c.SITE_ID_RE),
    _date("inicio_utc"),
    _date("fin_utc"),
    _dec("horas_sol", 0.0, _HOURS_CAP),
    _dec("pct_sol", c.PCT_MIN, c.PCT_MAX),
    _dec("sol_el_max_deg", c.EL_MIN, c.EL_MAX),
    _dec("sol_el_media_deg", c.EL_MIN, c.EL_MAX, required=False),
    _dec("horas_tierra_visible", 0.0, _HOURS_CAP),
    _dec("pct_tierra", c.PCT_MIN, c.PCT_MAX),
]

SCHEMAS = {
    c.TIPO_SITES: SITES_SCHEMA,
    c.TIPO_EFEMERIDES: EFEMERIDES_SCHEMA,
    c.TIPO_METRICAS: METRICAS_SCHEMA,
}

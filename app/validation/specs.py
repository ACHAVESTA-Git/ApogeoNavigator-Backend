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
    ColumnSpec(c.COL_SITE_ID, REGEX, pattern=c.SITE_ID_RE, unique=True),
    _text(c.COL_NOMBRE, *c.LEN_NOMBRE),
    ColumnSpec(c.COL_CATEGORIA, ENUM, choices=c.SITE_CATEGORIAS),
    _text(c.COL_MISION, *c.LEN_MISION),
    _text(c.COL_AGENCIA, *c.LEN_AGENCIA),
    _dec(c.COL_LAT, c.LAT_MIN, c.LAT_MAX),
    _dec(c.COL_LON, c.LON_MIN, c.LON_MAX),
    _dec(c.COL_ALT, c.ALT_KM_MIN, c.ALT_KM_MAX, required=False, default=c.ALT_KM_DEFAULT),
    ColumnSpec(c.COL_FUENTE, URL, min_len=c.LEN_URL[0], max_len=c.LEN_URL[1]),
    _text(c.COL_NOTAS, *c.LEN_NOTAS, required=False),
]

EFEMERIDES_SCHEMA = [
    _date(c.COL_UTC),
    _dec(c.COL_SOL_AZ, c.AZ_MIN, c.AZ_MAX, hi_inclusive=False),
    _dec(c.COL_SOL_EL, c.EL_MIN, c.EL_MAX),
    _dec(c.COL_TIERRA_AZ, c.AZ_MIN, c.AZ_MAX, hi_inclusive=False),
    _dec(c.COL_TIERRA_EL, c.EL_MIN, c.EL_MAX),
]

# Las horas se acotan contra el rango real en la coherencia; aquí solo el mínimo.
_HOURS_CAP = float(c.METRICS_MAX_RANGE_DAYS * c.HOURS_PER_DAY)

METRICAS_SCHEMA = [
    ColumnSpec(c.COL_SITE_ID, REGEX, pattern=c.SITE_ID_RE),
    _date(c.COL_INICIO),
    _date(c.COL_FIN),
    _dec(c.COL_HORAS_SOL, 0.0, _HOURS_CAP),
    _dec(c.COL_PCT_SOL, c.PCT_MIN, c.PCT_MAX),
    _dec(c.COL_SOL_EL_MAX, c.EL_MIN, c.EL_MAX),
    _dec(c.COL_SOL_EL_MEDIA, c.EL_MIN, c.EL_MAX, required=False),
    _dec(c.COL_HORAS_TIERRA, 0.0, _HOURS_CAP),
    _dec(c.COL_PCT_TIERRA, c.PCT_MIN, c.PCT_MAX),
]

SCHEMAS = {
    c.TIPO_SITES: SITES_SCHEMA,
    c.TIPO_EFEMERIDES: EFEMERIDES_SCHEMA,
    c.TIPO_METRICAS: METRICAS_SCHEMA,
}

# Los esquemas deben coincidir con las columnas declaradas en config (estándar §2-§4).
for _schema, _columns in ((SITES_SCHEMA, c.SITES_COLUMNS), (EFEMERIDES_SCHEMA, c.EPHEM_COLUMNS),
                          (METRICAS_SCHEMA, c.METRICS_COLUMNS)):
    assert tuple(spec.name for spec in _schema) == _columns, _columns

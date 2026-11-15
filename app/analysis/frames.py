"""Construcción de LazyFrames a partir de las efemérides ya validadas o de la verdad de terreno en Parquet.

No lee CSV por su cuenta: reutiliza `services.lectura.ephemeris`, que valida con el mismo código que la carga.
"""
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import polars as pl

from app.core import config as c

T_DAYS = "t_dias"
SOL_VISIBLE = "sol_visible"
TIERRA_VISIBLE = "tierra_visible"
UTC_DTYPE = pl.Datetime("us", "UTC")
VALUE_COLUMNS = (c.COL_SOL_AZ, c.COL_SOL_EL, c.COL_TIERRA_AZ, c.COL_TIERRA_EL)


def with_time_axis(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Añade `t_dias`: días transcurridos desde la primera fila."""
    elapsed = pl.col(c.COL_UTC) - pl.col(c.COL_UTC).first()
    return frame.with_columns((elapsed.dt.total_seconds() / (c.SECONDS_PER_HOUR * c.HOURS_PER_DAY)).alias(T_DAYS))


def from_rows(rows: Sequence[dict[str, Any]]) -> pl.LazyFrame:
    """LazyFrame ordenado por utc desde las filas validadas (utc con zona UTC)."""
    frame = pl.DataFrame(list(rows), schema_overrides={c.COL_UTC: UTC_DTYPE}).select(c.COL_UTC, *VALUE_COLUMNS)
    return with_time_axis(frame.lazy().sort(c.COL_UTC))


def from_parquet(path: Path, site_id: str) -> pl.LazyFrame:
    """Verdad de terreno de un sitio (Parquet con la columna `site_id`)."""
    lazy = pl.scan_parquet(path).filter(pl.col("site_id") == site_id).select(c.COL_UTC, *VALUE_COLUMNS)
    return with_time_axis(lazy.with_columns(pl.col(c.COL_UTC).dt.cast_time_unit("us")).sort(c.COL_UTC))

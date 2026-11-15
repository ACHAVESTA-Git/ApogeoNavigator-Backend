"""Pipeline exploratorio: funciones puras sobre LazyFrame (ventanas, día lunar, estacionalidad, libración)."""
import polars as pl

from app.analysis.frames import SOL_VISIBLE, T_DAYS, TIERRA_VISIBLE
from app.core import config as c

WINDOW_START, WINDOW_END, WINDOW_HOURS = c.COL_WINDOW_START, c.COL_WINDOW_END, c.COL_WINDOW_HOURS
WINDOW_COMPLETE, WINDOW_VISIBLE = c.COL_WINDOW_COMPLETE, c.COL_WINDOW_VISIBLE
EVENT_KIND, KIND_SET, KIND_RISE = c.COL_EVENT_KIND, c.EVENT_SET, c.EVENT_RISE
LUNAR_DAY = c.COL_LUNAR_DAY


def step_hours(frame: pl.LazyFrame) -> float:
    """Paso mediano entre filas, en horas."""
    seconds = frame.select(pl.col(c.COL_UTC).diff().dt.total_seconds().median()).collect().item()
    return seconds / c.SECONDS_PER_HOUR


def with_visibility(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.with_columns((pl.col(c.COL_SOL_EL) > 0).alias(SOL_VISIBLE),
                              (pl.col(c.COL_TIERRA_EL) > 0).alias(TIERRA_VISIBLE))


def windows(frame: pl.LazyFrame, visible_column: str) -> pl.LazyFrame:
    """Tramos consecutivos con el mismo estado de visibilidad.

    `fin` es la primera muestra del tramo siguiente (o la última muestra, en el tramo final). Solo los
    tramos con cambio de estado a ambos lados son `completa`: los extremos están cortados por el rango.
    """
    state = pl.col(visible_column)
    runs = frame.select(pl.col(c.COL_UTC), state.alias(WINDOW_VISIBLE),
                        (state != state.shift(1)).fill_null(True).cum_sum().alias(c.COL_RUN))
    grouped = runs.group_by(c.COL_RUN, maintain_order=True).agg(
        pl.col(WINDOW_VISIBLE).first(), pl.col(c.COL_UTC).first().alias(WINDOW_START),
        pl.col(c.COL_UTC).last().alias(c.COL_LAST_SAMPLE))
    ended = grouped.with_columns(pl.col(WINDOW_START).shift(-1).fill_null(pl.col(c.COL_LAST_SAMPLE)).alias(WINDOW_END))
    return ended.select(
        pl.col(WINDOW_VISIBLE), pl.col(WINDOW_START), pl.col(WINDOW_END),
        ((pl.col(WINDOW_END) - pl.col(WINDOW_START)).dt.total_seconds() / c.SECONDS_PER_HOUR).alias(WINDOW_HOURS),
        ((pl.col(c.COL_RUN) > 1) & (pl.col(c.COL_RUN) < pl.col(c.COL_RUN).max())).alias(WINDOW_COMPLETE))


def transitions(frame: pl.LazyFrame, visible_column: str) -> pl.LazyFrame:
    """Instantes en que cambia la visibilidad: `puesta` (>0 a ≤0) o `salida` (≤0 a >0)."""
    state = pl.col(visible_column)
    changed = frame.select(pl.col(c.COL_UTC), state.alias(c.COL_STATE_NOW), state.shift(1).alias(c.COL_STATE_BEFORE)).filter(
        pl.col(c.COL_STATE_BEFORE).is_not_null() & (pl.col(c.COL_STATE_NOW) != pl.col(c.COL_STATE_BEFORE)))
    return changed.select(pl.col(c.COL_UTC), pl.when(pl.col(c.COL_STATE_NOW)).then(pl.lit(KIND_RISE)).otherwise(
        pl.lit(KIND_SET)).alias(EVENT_KIND))


def lunar_day_stats(frame: pl.LazyFrame, step_h: float, period_days: float = c.LUNAR_DAY_DAYS) -> pl.LazyFrame:
    """Estadísticas por día lunar (bloques consecutivos de un mes sinódico desde la primera fila)."""
    block = (pl.col(T_DAYS) / period_days).floor().cast(pl.Int64).alias(LUNAR_DAY)
    return (with_visibility(frame).with_columns(block).group_by(LUNAR_DAY).agg(
        pl.col(c.COL_UTC).min().alias(WINDOW_START), pl.len().alias(c.COL_SAMPLES),
        (pl.col(SOL_VISIBLE).sum() * step_h).alias(c.COL_HORAS_SOL),
        (pl.col(TIERRA_VISIBLE).sum() * step_h).alias(c.COL_HORAS_TIERRA),
        pl.col(c.COL_SOL_EL).max().alias(c.COL_SOL_EL_MAX),
        pl.col(c.COL_SOL_EL).filter(pl.col(SOL_VISIBLE)).mean().alias(c.COL_SOL_EL_MEDIA)).sort(LUNAR_DAY))


def daily_extremes(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Elevación solar máxima, mínima y media por día UTC (estacionalidad por la inclinación del eje)."""
    day = pl.col(c.COL_UTC).dt.date().alias(c.COL_DATE)
    return frame.group_by(day).agg(
        pl.col(c.COL_SOL_EL).max().alias(c.COL_DAY_SUN_MAX), pl.col(c.COL_SOL_EL).min().alias(c.COL_DAY_SUN_MIN),
        pl.col(c.COL_SOL_EL).mean().alias(c.COL_DAY_SUN_MEAN)).sort(c.COL_DATE)


def monthly_percentages(frame: pl.LazyFrame) -> pl.LazyFrame:
    """% de Sol y de Tierra por mes natural UTC."""
    return (with_visibility(frame).sort(c.COL_UTC).group_by_dynamic(c.COL_UTC, every="1mo").agg(
        (pl.col(SOL_VISIBLE).mean() * c.PCT_FULL_SCALE).alias(c.COL_PCT_SOL),
        (pl.col(TIERRA_VISIBLE).mean() * c.PCT_FULL_SCALE).alias(c.COL_PCT_TIERRA),
        pl.len().alias(c.COL_SAMPLES)))


def _centered_azimuth(column: str) -> pl.Expr:
    """Azimut respecto a su media circular, en (−180, 180]."""
    rad = pl.col(column).radians()
    mean = pl.arctan2(rad.sin().mean(), rad.cos().mean()).degrees()
    return ((pl.col(column) - mean + 180) % 360) - 180


def libration(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Oscilación de la Tierra en el cielo: rango y desviación típica de su azimut y su elevación."""
    az = _centered_azimuth(c.COL_TIERRA_AZ)
    el = pl.col(c.COL_TIERRA_EL)
    return frame.select(
        az.max().sub(az.min()).alias(c.COL_LIB_AZ_RANGE), az.std().alias(c.COL_LIB_AZ_STD),
        el.max().sub(el.min()).alias(c.COL_LIB_EL_RANGE), el.std().alias(c.COL_LIB_EL_STD),
        el.mean().alias(c.COL_LIB_EL_MEAN))

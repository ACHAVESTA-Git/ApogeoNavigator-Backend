"""Ventanas de estancia sobre una serie horaria: horas de Sol, de Tierra y de ambos a la vez para cada hora de llegada."""
from datetime import datetime
from typing import Sequence

import numpy as np
import polars as pl
from numpy.lib.stride_tricks import sliding_window_view

from app.core import config as c

COL_IDX = "idx"
COL_N_SOL, COL_N_TIERRA, COL_N_AMBOS = "n_sol", "n_tierra", "n_ambos"
ORDEN_MEJOR = ([c.KEY_HORAS_SOL_Y_TIERRA, c.COL_HORAS_SOL, COL_IDX], [True, True, False])


def _suma_hacia_delante(columna: str, n: int) -> pl.Expr:
    """Suma de las `n` muestras que empiezan en cada fila (nula si la ventana se sale de la serie)."""
    return pl.col(columna).cast(pl.Int32).reverse().rolling_sum(window_size=n).reverse()


def racha_sin_tierra(tierra_visible: np.ndarray, n: int) -> np.ndarray:
    """Racha continua más larga (en muestras) sin Tierra dentro de cada ventana completa de `n` muestras."""
    total = len(tierra_visible)
    if total < n:
        return np.zeros(0, dtype=np.int32)
    pos = np.arange(total)
    ultima_visible = np.maximum.accumulate(np.where(tierra_visible, pos, -1))
    termina_aqui = (pos - ultima_visible).astype(np.int32)       # longitud de la racha sin Tierra que acaba en cada muestra
    ventanas = sliding_window_view(termina_aqui, n)               # (total - n + 1, n)
    return np.minimum(ventanas, np.arange(1, n + 1, dtype=np.int32)).max(axis=1)


def evaluar_ventanas(utc: Sequence[datetime], sol_el: Sequence[float], tierra_el: Sequence[float], n: int, paso_h: float) -> pl.DataFrame:
    """Una fila por hora de llegada con ventana completa de `n` muestras (la estancia cabe en la serie).

    Columnas: idx, utc, horas_sol, horas_tierra, horas_sol_y_tierra, sol_al_llegar, tierra_al_llegar, racha_sin_tierra_h.
    Una muestra cuenta `paso_h` horas. Elevación > 0 = visible."""
    sol = np.asarray(sol_el, dtype=float) > 0
    tierra = np.asarray(tierra_el, dtype=float) > 0
    base = pl.DataFrame({c.COL_UTC: list(utc), COL_N_SOL: sol, COL_N_TIERRA: tierra, COL_N_AMBOS: sol & tierra})
    base = base.with_row_index(COL_IDX).with_columns(
        _suma_hacia_delante(COL_N_SOL, n).alias(COL_N_SOL),
        _suma_hacia_delante(COL_N_TIERRA, n).alias(COL_N_TIERRA),
        _suma_hacia_delante(COL_N_AMBOS, n).alias(COL_N_AMBOS),
        pl.Series(c.KEY_SOL_AL_LLEGAR, sol),
        pl.Series(c.KEY_TIERRA_AL_LLEGAR, tierra),
    ).drop_nulls(COL_N_SOL)
    rachas = racha_sin_tierra(tierra, n)
    return base.with_columns(
        (pl.col(COL_N_SOL) * paso_h).round(c.ROUND_HOURS).alias(c.COL_HORAS_SOL),
        (pl.col(COL_N_TIERRA) * paso_h).round(c.ROUND_HOURS).alias(c.KEY_HORAS_TIERRA),
        (pl.col(COL_N_AMBOS) * paso_h).round(c.ROUND_HOURS).alias(c.KEY_HORAS_SOL_Y_TIERRA),
        pl.Series(c.KEY_RACHA_SIN_TIERRA, (rachas * paso_h).round(c.ROUND_HOURS)),
    ).drop(COL_N_SOL, COL_N_TIERRA, COL_N_AMBOS)


def ordenar_mejores(ventanas: pl.DataFrame) -> pl.DataFrame:
    """Mejor primero: más horas con Sol y Tierra, a igualdad más horas de Sol y, si sigue el empate, la llegada más temprana."""
    return ventanas.sort(ORDEN_MEJOR[0], descending=ORDEN_MEJOR[1])


def mejores_no_solapadas(ordenadas: pl.DataFrame, n: int, k: int = c.VENTANAS_TOP) -> list[dict]:
    """Primeras `k` ventanas de `ordenadas` que no se solapan entre sí (dos ventanas solapan si sus llegadas distan menos de `n`)."""
    elegidas: list[dict] = []
    for fila in ordenadas.iter_rows(named=True):
        if all(abs(fila[COL_IDX] - e[COL_IDX]) >= n for e in elegidas):
            elegidas.append(fila)
            if len(elegidas) == k:
                break
    return elegidas


def mejor_por_dia(ordenadas: pl.DataFrame) -> list[dict]:
    """La mejor ventana de cada día UTC de llegada, en orden cronológico."""
    con_dia = ordenadas.with_columns(pl.col(c.COL_UTC).dt.strftime("%Y-%m-%d").alias(c.KEY_DIA_UTC))
    return con_dia.unique(subset=c.KEY_DIA_UTC, keep="first", maintain_order=True).sort(c.KEY_DIA_UTC).to_dicts()

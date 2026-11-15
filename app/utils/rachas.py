"""Rachas continuas sobre una serie de muestras con paso constante (indicadores para astronautas)."""
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from app.core import config as c

Row = dict[str, Any]


def racha_mas_larga(filas: Sequence[Row], cumple: Callable[[Row], bool], paso_h: float, fin_serie: datetime) -> dict:
    """Racha continua más larga de filas que cumplen `cumple` (la primera si hay empate).

    Cada muestra aporta `paso_h` horas. La racha termina en la primera muestra que deja de cumplir, o en `fin_serie`
    si llega al final de la serie. `trunca_inicio` / `trunca_fin` avisan de que la racha toca un borde de la serie,
    así que puede ser más larga fuera de ella. Sin ninguna racha: 0 horas y fechas nulas."""
    mejor_ini, mejor_n, ini = 0, 0, None
    for i, fila in enumerate(list(filas) + [None]):
        if fila is not None and cumple(fila):
            ini = i if ini is None else ini
            continue
        if ini is not None and i - ini > mejor_n:
            mejor_ini, mejor_n = ini, i - ini
        ini = None
    if mejor_n == 0:
        return {c.KEY_HORAS: 0.0, c.COL_INICIO: None, c.COL_FIN: None, c.KEY_TRUNCA_INICIO: False, c.KEY_TRUNCA_FIN: False}
    fin_idx = mejor_ini + mejor_n
    termina_en_borde = fin_idx >= len(filas)
    return {
        c.KEY_HORAS: round(mejor_n * paso_h, c.ROUND_HOURS),
        c.COL_INICIO: filas[mejor_ini][c.COL_UTC],
        c.COL_FIN: fin_serie if termina_en_borde else filas[fin_idx][c.COL_UTC],
        c.KEY_TRUNCA_INICIO: mejor_ini == 0,
        c.KEY_TRUNCA_FIN: termina_en_borde,
    }

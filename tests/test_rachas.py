"""Rachas continuas e indicadores para astronautas: casos pequeños a mano y una consulta real de la API."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core import config as c
from app.main import app
from app.services import lectura
from app.utils.rachas import racha_mas_larga

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)


def _filas(sol, tierra, paso_h=1.0):
    return [{c.COL_UTC: T0 + timedelta(hours=paso_h * i), c.COL_SOL_EL: s, c.COL_TIERRA_EL: t}
            for i, (s, t) in enumerate(zip(sol, tierra))]


def _fin(filas, paso_h=1.0):
    return filas[-1][c.COL_UTC] + timedelta(hours=paso_h)


def test_racha_en_el_medio_no_esta_truncada():
    filas = _filas([1, -1, -1, -1, 1, 1], [1] * 6)
    r = racha_mas_larga(filas, lambda f: f[c.COL_SOL_EL] <= 0, 1.0)
    assert r[c.KEY_HORAS] == 3.0
    assert r[c.COL_INICIO] == T0 + timedelta(hours=1) and r[c.COL_FIN] == T0 + timedelta(hours=4)
    assert (r[c.KEY_TRUNCA_INICIO], r[c.KEY_TRUNCA_FIN]) == (False, False)


def test_racha_que_llega_al_final_termina_un_paso_despues_de_la_ultima_fila_y_esta_truncada():
    filas = _filas([1, -1, -1], [1] * 3)
    r = racha_mas_larga(filas, lambda f: f[c.COL_SOL_EL] <= 0, 1.0)
    assert r[c.KEY_HORAS] == 2.0 and r[c.COL_FIN] == _fin(filas)
    assert r[c.COL_FIN] - r[c.COL_INICIO] == timedelta(hours=r[c.KEY_HORAS])
    assert (r[c.KEY_TRUNCA_INICIO], r[c.KEY_TRUNCA_FIN]) == (False, True)


def test_racha_desde_la_primera_muestra_esta_truncada_al_inicio():
    filas = _filas([-1, -1, 1], [1] * 3)
    r = racha_mas_larga(filas, lambda f: f[c.COL_SOL_EL] <= 0, 1.0)
    assert r[c.COL_INICIO] == T0 and r[c.KEY_TRUNCA_INICIO] is True and r[c.KEY_TRUNCA_FIN] is False


def test_en_un_empate_gana_la_primera_racha():
    filas = _filas([-1, -1, 1, -1, -1, 1], [1] * 6)
    r = racha_mas_larga(filas, lambda f: f[c.COL_SOL_EL] <= 0, 1.0)
    assert r[c.COL_INICIO] == T0


def test_sin_racha_devuelve_cero_y_fechas_nulas():
    filas = _filas([1, 2, 3], [1] * 3)
    r = racha_mas_larga(filas, lambda f: f[c.COL_SOL_EL] <= 0, 1.0)
    assert r == {c.KEY_HORAS: 0.0, c.COL_INICIO: None, c.COL_FIN: None, c.KEY_TRUNCA_INICIO: False, c.KEY_TRUNCA_FIN: False}


def test_el_paso_de_la_serie_multiplica_las_horas():
    filas = _filas([1, -1, -1, 1], [1] * 4, paso_h=6.0)
    r = racha_mas_larga(filas, lambda f: f[c.COL_SOL_EL] <= 0, 6.0)
    assert r[c.KEY_HORAS] == 12.0


def test_una_serie_vacia_no_tiene_racha():
    assert racha_mas_larga([], lambda f: True, 1.0)[c.KEY_HORAS] == 0.0


def test_indicadores_combinan_sol_y_tierra():
    #            sol:   +  +  -  -  -  +  +  +
    #          tierra:  +  -  -  +  +  +  +  -
    filas = _filas([1, 1, -1, -1, -1, 1, 1, 1], [1, -1, -1, 1, 1, 1, 1, -1])
    ind = lectura.astronaut_indicators(filas, 1.0, 1.5)
    assert ind[c.KEY_NOCHE_LARGA][c.KEY_HORAS] == 3.0
    assert ind[c.KEY_APAGON_TIERRA][c.KEY_HORAS] == 2.0
    assert ind[c.KEY_VENTANA_SOL_TIERRA][c.KEY_HORAS] == 2.0
    assert ind[c.KEY_VENTANA_SOL_TIERRA][c.COL_INICIO] == lectura.fmt(T0 + timedelta(hours=5))
    assert ind[c.KEY_PUESTA_SOL] == lectura.fmt(T0 + timedelta(hours=2))
    assert ind[c.KEY_PUESTA_TIERRA] == lectura.fmt(T0 + timedelta(hours=1))
    assert ind[c.KEY_SOL_EL_MEDIA_DIA] == 1.5


def test_sol_siempre_arriba_no_tiene_noche_ni_puesta():
    filas = _filas([1, 2, 3], [-1, -1, -1])
    ind = lectura.astronaut_indicators(filas, 1.0, 2.0)
    assert ind[c.KEY_NOCHE_LARGA][c.KEY_HORAS] == 0.0 and ind[c.KEY_PUESTA_SOL] is None
    assert ind[c.KEY_VENTANA_SOL_TIERRA][c.KEY_HORAS] == 0.0
    assert ind[c.KEY_APAGON_TIERRA][c.KEY_HORAS] == 3.0 and ind[c.KEY_PUESTA_TIERRA] is None


def test_la_api_expone_los_indicadores_sin_cambiar_lo_demas():
    cliente = TestClient(app)
    r = cliente.get("/api/metricas", params={"site_id": "shackleton"})
    assert r.status_code == 200
    item = r.json()["resultados"][0]
    ind = item["indicadores_astronauta"]
    assert set(ind) == {"noche_mas_larga", "apagon_tierra_mas_largo", "ventana_sol_y_tierra", "proxima_puesta_sol_utc",
                        "proxima_puesta_tierra_utc", "sol_el_media_horas_sol_deg"}
    assert ind["sol_el_media_horas_sol_deg"] == item["sol_el_media_deg"]
    # Con el Sol visible el 100 % del rango, no hay noche; el apagón y la ventana no pueden pasar del total de horas.
    assert ind["noche_mas_larga"]["horas"] == (0.0 if item["pct_sol"] == 100.0 else ind["noche_mas_larga"]["horas"])
    total = item["horas_sol"] / (item["pct_sol"] / 100) if item["pct_sol"] else None
    assert total is None or ind["apagon_tierra_mas_largo"]["horas"] <= total
    assert ind["ventana_sol_y_tierra"]["horas"] <= min(item["horas_sol"], item["horas_tierra_visible"])

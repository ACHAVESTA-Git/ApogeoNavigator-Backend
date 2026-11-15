"""Ventanas de alunizaje: casos sintéticos con respuesta calculada a mano, contraste con fuerza bruta y la API real."""
import random
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import config as c
from app.main import app
from app.utils import ventanas as util

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
RUTA = "/api/ventanas-alunizaje"


def _serie(sol, tierra):
    utc = [T0 + timedelta(hours=i) for i in range(len(sol))]
    return utc, [1.0 if s else -1.0 for s in sol], [1.0 if t else -1.0 for t in tierra]


def _evaluar(sol, tierra, n, paso_h=1.0):
    return util.evaluar_ventanas(*_serie(sol, tierra), n, paso_h)


def test_ventana_a_mano():
    #        idx: 0  1  2  3  4  5
    sol = [1, 1, 0, 1, 1, 1]
    tierra = [1, 0, 0, 1, 1, 0]
    df = _evaluar(sol, tierra, 3)
    assert df.height == 4                                  # llegadas 0..3: la estancia cabe entera
    filas = df.to_dicts()
    assert [f[c.COL_HORAS_SOL] for f in filas] == [2.0, 2.0, 2.0, 3.0]
    assert [f[c.KEY_HORAS_TIERRA] for f in filas] == [1.0, 1.0, 2.0, 2.0]
    assert [f[c.KEY_HORAS_SOL_Y_TIERRA] for f in filas] == [1.0, 1.0, 2.0, 2.0]
    assert [f[c.KEY_RACHA_SIN_TIERRA] for f in filas] == [2.0, 2.0, 1.0, 1.0]
    assert [f[c.KEY_SOL_AL_LLEGAR] for f in filas] == [True, True, False, True]
    assert [f[c.KEY_TIERRA_AL_LLEGAR] for f in filas] == [True, False, False, True]


def test_el_paso_de_la_serie_multiplica_las_horas():
    df = _evaluar([1, 1, 1, 1], [1, 1, 0, 0], 2, paso_h=6.0)
    assert df[c.COL_HORAS_SOL].to_list() == [12.0, 12.0, 12.0]
    assert df[c.KEY_RACHA_SIN_TIERRA].to_list() == [0.0, 6.0, 12.0]


def test_una_estancia_mas_larga_que_la_serie_no_tiene_ventanas():
    assert _evaluar([1, 1], [1, 1], 3).height == 0


def test_estancia_igual_a_la_serie_da_una_sola_ventana():
    df = _evaluar([1, 0, 1], [0, 0, 1], 3)
    assert df.height == 1 and df[c.KEY_RACHA_SIN_TIERRA][0] == 2.0


@pytest.mark.parametrize("semilla", range(5))
def test_contra_fuerza_bruta(semilla):
    azar = random.Random(semilla)
    sol = [azar.random() < 0.6 for _ in range(120)]
    tierra = [azar.random() < 0.5 for _ in range(120)]
    n = 17
    df = _evaluar(sol, tierra, n).to_dicts()
    assert len(df) == len(sol) - n + 1
    for fila in df:
        i = fila[util.COL_IDX]
        s, t = sol[i:i + n], tierra[i:i + n]
        assert fila[c.COL_HORAS_SOL] == sum(s)
        assert fila[c.KEY_HORAS_TIERRA] == sum(t)
        assert fila[c.KEY_HORAS_SOL_Y_TIERRA] == sum(a and b for a, b in zip(s, t))
        mejor = actual = 0
        for visible in t:
            actual = 0 if visible else actual + 1
            mejor = max(mejor, actual)
        assert fila[c.KEY_RACHA_SIN_TIERRA] == mejor


def test_orden_de_mejores_desempata_por_sol_y_luego_por_llegada():
    #         sol:    1  1  1  0  1  1  0  0       n = 2: ventanas (idx: sol+tierra, sol)
    sol = [1, 1, 1, 0, 1, 1, 0, 0]
    tierra = [1, 1, 0, 0, 1, 1, 1, 1]
    orden = util.ordenar_mejores(_evaluar(sol, tierra, 2))
    # Con 2 horas de Sol y Tierra: llegadas 0 y 4. Empatan en horas de Sol; gana la más temprana.
    assert orden[util.COL_IDX].to_list()[:2] == [0, 4]
    # Con 1 hora de ambos: la llegada 5 (Sol 1) va detrás de la 1 (Sol 2) aunque llegue después de empatar en ambos.
    assert orden[util.COL_IDX].to_list().index(1) < orden[util.COL_IDX].to_list().index(5)


def test_mejores_no_se_solapan_y_son_como_mucho_tres():
    sol = [1] * 40
    tierra = [1] * 40
    ordenadas = util.ordenar_mejores(_evaluar(sol, tierra, 10))
    elegidas = util.mejores_no_solapadas(ordenadas, 10)
    idx = sorted(f[util.COL_IDX] for f in elegidas)
    assert len(idx) == c.VENTANAS_TOP
    assert all(b - a >= 10 for a, b in zip(idx, idx[1:]))
    assert idx == [0, 10, 20]                                # todas empatan: gana la más temprana y se encadenan sin solape


def test_mejores_devuelve_menos_si_no_caben_tres():
    ordenadas = util.ordenar_mejores(_evaluar([1] * 15, [1] * 15, 10))
    assert len(util.mejores_no_solapadas(ordenadas, 10)) == 1   # 6 llegadas, todas se solapan con la primera


def test_mejor_por_dia_elige_la_mejor_hora_de_cada_dia():
    horas = 24 * 3
    sol = [1] * horas
    tierra = [0] * 10 + [1] * 30 + [0] * (horas - 40)       # la Tierra se ve de la hora 10 a la 39
    dias = util.mejor_por_dia(util.ordenar_mejores(_evaluar(sol, tierra, 12)))
    assert [d[c.KEY_DIA_UTC] for d in dias] == ["2026-12-01", "2026-12-02", "2026-12-03"]   # llegadas 0..60 de 12 h
    assert dias[0][util.COL_IDX] == 10 and dias[0][c.KEY_HORAS_SOL_Y_TIERRA] == 12.0


# ---------------------------------------------------------------- API
@pytest.fixture(scope="module")
def cliente():
    return TestClient(app)


def _pedir(cliente, **params):
    return cliente.get(RUTA, params=params)


def test_un_sitio_trae_serie_diaria_mejores_y_marca_de_truncado(cliente):
    r = _pedir(cliente, site_id="apollo11", estancia_h="72")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estancia_h"] == 72 and cuerpo["mejor_global"] is None
    sitio = cuerpo["resultados"][0]
    assert sitio["trunca"] is True and sitio["ventanas_descartadas"] == 71
    assert len(sitio["mejores"]) == 3
    horas = [m["horas_sol_y_tierra"] for m in sitio["mejores"]]
    assert horas == sorted(horas, reverse=True)
    llegadas = sorted(datetime.strptime(m["llegada_utc"], "%Y-%m-%dT%H:%MZ") for m in sitio["mejores"])
    assert all(b - a >= timedelta(hours=72) for a, b in zip(llegadas, llegadas[1:]))
    for m in sitio["mejores"]:
        assert m["pct_sol_y_tierra"] == round(m["horas_sol_y_tierra"] / 72 * 100, 2)
        assert m["horas_sol_y_tierra"] <= min(m["horas_sol"], m["horas_tierra"])
    dias = [d["dia_utc"] for d in sitio["serie_diaria"]]
    assert dias == sorted(set(dias))


def test_ninguna_ventana_sale_de_los_datos(cliente):
    sitio = _pedir(cliente, site_id="shackleton", estancia_h="336").json()["resultados"][0]
    fin = datetime.strptime(sitio["fin_utc"], "%Y-%m-%dT%H:%MZ")
    for m in sitio["mejores"]:
        assert datetime.strptime(m["salida_utc"], "%Y-%m-%dT%H:%MZ") <= fin + timedelta(hours=sitio["paso_horas"])


def test_con_varios_sitios_hay_mejor_global_y_es_el_mejor_de_los_primeros(cliente):
    cuerpo = _pedir(cliente, site_id=["shackleton", "apollo11", "change4"], estancia_h="156").json()
    assert [r["site_id"] for r in cuerpo["resultados"]] == ["shackleton", "apollo11", "change4"]
    primeros = [r["mejores"][0]["horas_sol_y_tierra"] for r in cuerpo["resultados"] if r["mejores"]]
    assert cuerpo["mejor_global"]["ventana"]["horas_sol_y_tierra"] == max(primeros)
    assert cuerpo["mejor_global"]["site_id"] in {"shackleton", "apollo11"}   # Chang'e 4 (cara oculta) nunca ve la Tierra
    ocultas = cuerpo["resultados"][2]["mejores"][0]
    assert ocultas["horas_sol_y_tierra"] == 0.0 and ocultas["racha_sin_tierra_h"] == 156.0


def test_un_rango_corto_descarta_las_estancias_que_no_caben(cliente):
    sitio = _pedir(cliente, site_id="apollo11", estancia_h="72", desde="2026-12-01T00:00Z", hasta="2026-12-05T00:00Z").json()["resultados"][0]
    assert sitio["ventanas_evaluadas"] == 97 - 71 and sitio["trunca"] is True
    assert sitio["serie_diaria"][-1]["dia_utc"] == "2026-12-02"


def test_un_rango_menor_que_la_estancia_no_tiene_ventanas(cliente):
    sitio = _pedir(cliente, site_id="apollo11", estancia_h="336", desde="2026-12-01T00:00Z", hasta="2026-12-03T00:00Z").json()["resultados"][0]
    assert sitio["mejores"] == [] and sitio["serie_diaria"] == [] and sitio["ventanas_evaluadas"] == 0 and sitio["trunca"] is True


def test_un_rango_con_un_solo_sitio_sin_ventanas_deja_mejor_global_nulo(cliente):
    cuerpo = _pedir(cliente, site_id=["apollo11", "slim"], estancia_h="336", desde="2026-12-01T00:00Z", hasta="2026-12-03T00:00Z").json()
    assert cuerpo["mejor_global"] is None


@pytest.mark.parametrize("params,codigo", [
    ({"site_id": "apollo11"}, "ESTANCIA_INVALIDA"),
    ({"site_id": "apollo11", "estancia_h": "100"}, "ESTANCIA_INVALIDA"),
    ({"site_id": "apollo11", "estancia_h": "tres"}, "ESTANCIA_INVALIDA"),
    ({"site_id": "apollo11", "estancia_h": "72", "desde": "2027-01-01T00:00Z", "hasta": "2026-12-01T00:00Z"}, "RANGO_FECHAS_INVALIDO"),
    ({"site_id": "apollo11", "estancia_h": "72", "desde": "ayer"}, "RANGO_FECHAS_INVALIDO"),
    ({"site_id": "apollo11", "estancia_h": "72", "hasta": "2030-01-01T00:00Z"}, "FECHA_FUERA_DE_DATOS"),
])
def test_errores_con_el_estandar(cliente, params, codigo):
    r = _pedir(cliente, **params)
    assert r.status_code == 422 and r.json()["error"]["codigo"] == codigo
    assert isinstance(r.json()["detail"], str)


def test_sitios_requeridos_y_limite_de_cinco(cliente):
    assert _pedir(cliente, estancia_h="72").status_code == 422
    seis = ["apollo11", "apollo15", "apollo17", "shackleton", "slim", "change4"]
    r = _pedir(cliente, site_id=seis, estancia_h="72")
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "COMPARACION_SITIOS_INVALIDA"
    assert _pedir(cliente, site_id=seis[:5], estancia_h="72").status_code == 200
    assert _pedir(cliente, site_id="no_existe", estancia_h="72").status_code == 404

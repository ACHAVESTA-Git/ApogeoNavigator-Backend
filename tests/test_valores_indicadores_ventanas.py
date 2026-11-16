"""Pruebas de VALORES (no solo de forma): series sintéticas con el resultado calculado a mano, y la caché de ventanas."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import config as c
from app.main import app
from app.services import lectura
from app.services import ventanas as svc
from app.utils import ventanas as util

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)


def _t(horas):
    return lectura.fmt(T0 + timedelta(hours=horas))


def _filas(sol, tierra):
    return [{c.COL_UTC: T0 + timedelta(hours=i), c.COL_SOL_EL: s, c.COL_TIERRA_EL: t} for i, (s, t) in enumerate(zip(sol, tierra))]


# ------------------------------------------------------------ indicadores_astronauta
def test_indicadores_caso_a_valores_conocidos():
    sol = [5, 5, -1, -1, -1, -1, 3, 3, 3, -2, -2, 4]
    tierra = [2, -1, -1, 2, 2, 2, -3, -3, 2, 2, 2, 2]
    m = lectura.compute_metrics("sitio", _filas(sol, tierra), 1.0, T0, T0 + timedelta(hours=12))
    ind = m[c.KEY_INDICADORES]
    assert (m[c.COL_HORAS_SOL], m[c.COL_PCT_SOL], m[c.COL_HORAS_TIERRA], m[c.COL_PCT_TIERRA]) == (6.0, 50.0, 8.0, 66.67)
    assert ind[c.KEY_NOCHE_LARGA] == {c.KEY_HORAS: 4.0, c.COL_INICIO: _t(2), c.COL_FIN: _t(6), c.KEY_TRUNCA_INICIO: False, c.KEY_TRUNCA_FIN: False}
    assert ind[c.KEY_APAGON_TIERRA] == {c.KEY_HORAS: 2.0, c.COL_INICIO: _t(1), c.COL_FIN: _t(3), c.KEY_TRUNCA_INICIO: False, c.KEY_TRUNCA_FIN: False}
    assert ind[c.KEY_VENTANA_SOL_TIERRA] == {c.KEY_HORAS: 1.0, c.COL_INICIO: _t(0), c.COL_FIN: _t(1), c.KEY_TRUNCA_INICIO: True, c.KEY_TRUNCA_FIN: False}
    assert ind[c.KEY_PUESTA_SOL] == _t(2) and ind[c.KEY_PUESTA_TIERRA] == _t(1)
    assert ind[c.KEY_SOL_EL_MEDIA_DIA] == round(23 / 6, 4) == m[c.COL_SOL_EL_MEDIA]


def test_indicadores_caso_b_paso_de_6_horas_y_rachas_en_los_bordes():
    sol = [-1, -1, 2, 2, 2, -1]               # 6 muestras de 6 h = 36 h
    tierra = [3, 3, 3, -1, -1, -1]
    filas = [{c.COL_UTC: T0 + timedelta(hours=6 * i), c.COL_SOL_EL: s, c.COL_TIERRA_EL: t} for i, (s, t) in enumerate(zip(sol, tierra))]
    m = lectura.compute_metrics("sitio", filas, 6.0, T0, T0 + timedelta(hours=36))
    ind = m[c.KEY_INDICADORES]
    assert (m[c.COL_HORAS_SOL], m[c.COL_HORAS_TIERRA]) == (18.0, 18.0)
    assert ind[c.KEY_NOCHE_LARGA][c.KEY_HORAS] == 12.0 and ind[c.KEY_NOCHE_LARGA][c.KEY_TRUNCA_INICIO] is True
    assert ind[c.KEY_APAGON_TIERRA][c.KEY_HORAS] == 18.0 and ind[c.KEY_APAGON_TIERRA][c.KEY_TRUNCA_FIN] is True
    assert ind[c.KEY_APAGON_TIERRA][c.COL_FIN] == _t(36)                       # un paso (6 h) después de la última muestra
    assert ind[c.KEY_VENTANA_SOL_TIERRA] == {c.KEY_HORAS: 6.0, c.COL_INICIO: _t(12), c.COL_FIN: _t(18), c.KEY_TRUNCA_INICIO: False, c.KEY_TRUNCA_FIN: False}
    assert ind[c.KEY_PUESTA_SOL] == _t(30)                                     # arranca de noche: la primera puesta es tras la primera salida
    assert ind[c.KEY_PUESTA_TIERRA] == _t(18)
    assert ind[c.KEY_SOL_EL_MEDIA_DIA] == 2.0


# ------------------------------------------------------------ ventanas de alunizaje
def test_ventanas_caso_a_valores_conocidos():
    sol = [1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1]
    tierra = [0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1]
    df = util.evaluar_ventanas([T0 + timedelta(hours=i) for i in range(12)], [1.0 if s else -1.0 for s in sol],
                               [1.0 if t else -1.0 for t in tierra], 4, 1.0)
    assert df[c.KEY_HORAS_SOL_Y_TIERRA].to_list() == [3.0, 3.0, 2.0, 1.0, 1.0, 2.0, 3.0, 4.0, 4.0]
    assert df[c.COL_HORAS_SOL].to_list() == [4.0, 3.0, 2.0, 2.0, 2.0, 3.0, 4.0, 4.0, 4.0]
    mejores = util.mejores_no_solapadas(util.ordenar_mejores(df), 4)
    assert [f[util.COL_IDX] for f in mejores] == [7, 0]                       # 7 y 8 empatan: gana la más temprana; el resto solapa
    assert (mejores[0][c.KEY_RACHA_SIN_TIERRA], mejores[0][c.KEY_TIERRA_AL_LLEGAR]) == (0.0, True)
    assert (mejores[1][c.KEY_RACHA_SIN_TIERRA], mejores[1][c.KEY_TIERRA_AL_LLEGAR], mejores[1][c.KEY_SOL_AL_LLEGAR]) == (1.0, False, True)


def test_ventanas_caso_b_dias_y_porcentajes_con_estancia_real(tmp_path, monkeypatch):
    """Serie de 6 días con Sol siempre y Tierra solo los días 1 y 4: la mejor llegada de cada día y sus % son conocidos."""
    carpeta = tmp_path / "data"
    carpeta.mkdir()
    (carpeta / "sites.csv").write_bytes(open(os.path.join(os.path.dirname(__file__), "..", "data", "sites.csv"), "rb").read())
    lineas = ["utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg"]
    for i in range(24 * 6):
        dia = i // 24
        lineas.append(f"{_t(i)},10.0,5.0,20.0,{0.4 if dia in (0, 3) else -0.4}")
    (carpeta / "efemerides_apollo11.csv").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    monkeypatch.setenv(c.ENV_DATA_DIR, str(carpeta))
    lectura.clear_cache()
    svc.clear_cache()
    res = svc.landing_windows(["apollo11"], "72", None, None)["resultados"][0]
    assert res[c.KEY_VENTANAS_EVAL] == 144 - 71 and res[c.KEY_VENTANAS_DESCARTADAS] == 71 and res[c.KEY_TRUNCA] is True
    por_dia = {d[c.KEY_DIA_UTC]: d for d in res[c.KEY_SERIE_DIARIA]}
    # Llegar a las 00:00 del día 1 da 24 h con Tierra de 72 (1/3). Llegar el día 2 a las 00:00 solo ve la Tierra en el día 4 (24 h).
    assert por_dia["2026-12-01"][c.KEY_HORAS_SOL_Y_TIERRA] == 24.0 and por_dia["2026-12-01"][c.KEY_PCT_SOL_Y_TIERRA] == 33.33
    assert por_dia["2026-12-03"][c.KEY_LLEGADA] == "2026-12-03T00:00Z"           # 3 días de llegada hasta el día 6 = la Tierra del día 4 entra
    assert por_dia["2026-12-04"][c.KEY_HORAS_SOL_Y_TIERRA] == 24.0
    primera = res[c.KEY_MEJORES][0]
    assert primera[c.KEY_HORAS_SOL_Y_TIERRA] == 24.0 and primera[c.KEY_LLEGADA] == "2026-12-01T00:00Z"
    assert primera[c.KEY_SALIDA_VENTANA] == "2026-12-04T00:00Z" and primera[c.COL_PCT_SOL] == 100.0 and primera[c.COL_PCT_TIERRA] == 33.33
    assert primera[c.KEY_RACHA_SIN_TIERRA] == 48.0 and primera[c.KEY_TIERRA_AL_LLEGAR] is True


# ------------------------------------------------------------ caché de ventanas
@pytest.fixture
def cuenta(monkeypatch):
    llamadas = []
    original = svc._landing_windows
    monkeypatch.setattr(svc, "_landing_windows", lambda *a: (llamadas.append(a), original(*a))[1])
    svc.clear_cache()
    yield llamadas
    svc.clear_cache()


def test_pedir_lo_mismo_no_recalcula_y_otra_peticion_si(cuenta):
    primera = svc.landing_windows(["apollo11"], "72", None, None)
    assert svc.landing_windows(["apollo11"], "72", None, None) is primera and len(cuenta) == 1
    svc.landing_windows(["apollo11"], "156", None, None)
    svc.landing_windows(["apollo11", "slim"], "72", None, None)
    assert len(cuenta) == 3


def test_la_cache_caduca_con_el_ttl(cuenta, monkeypatch):
    svc.landing_windows(["apollo11"], "72", None, None)
    monkeypatch.setattr(c, "VENTANAS_CACHE_TTL_S", 0)
    svc.landing_windows(["apollo11"], "72", None, None)
    assert len(cuenta) == 2


def test_un_cambio_en_los_datos_invalida_la_cache(cuenta, tmp_path, monkeypatch):
    carpeta = tmp_path / "data"
    carpeta.mkdir()
    real = c.get_data_dir()
    for nombre in ("sites.csv", "efemerides_apollo11.csv"):
        (carpeta / nombre).write_bytes((real / nombre).read_bytes())
    monkeypatch.setenv(c.ENV_DATA_DIR, str(carpeta))
    lectura.clear_cache()
    svc.landing_windows(["apollo11"], "72", None, None)
    svc.landing_windows(["apollo11"], "72", None, None)
    assert len(cuenta) == 1
    ruta = carpeta / "efemerides_apollo11.csv"
    antes = ruta.stat()
    os.utime(ruta, ns=(antes.st_atime_ns, antes.st_mtime_ns + 5_000_000_000))
    svc.landing_windows(["apollo11"], "72", None, None)
    assert len(cuenta) == 2


def test_los_errores_no_se_guardan_y_la_cache_tiene_tope(cuenta, monkeypatch):
    for _ in range(2):
        with pytest.raises(lectura.LecturaError):
            svc.landing_windows(["apollo11"], "100", None, None)
    assert len(cuenta) == 2
    monkeypatch.setattr(c, "VENTANAS_CACHE_MAX_ENTRIES", 2)
    for desde in ("2026-12-01T00:00Z", "2026-12-02T00:00Z", "2026-12-03T00:00Z"):
        svc.landing_windows(["apollo11"], "72", desde, None)
    assert len(svc._cache) == 2


def test_la_api_con_cinco_sitios_y_336_horas_responde_y_la_segunda_es_instantanea():
    cliente = TestClient(app)
    sitios = ["apollo11", "apollo15", "apollo17", "shackleton", "slim"]
    svc.clear_cache()
    a = cliente.get("/api/ventanas-alunizaje", params={"site_id": sitios, "estancia_h": "336"})
    inicio = time.perf_counter()
    b = cliente.get("/api/ventanas-alunizaje", params={"site_id": sitios, "estancia_h": "336"})
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    assert time.perf_counter() - inicio < 1.0

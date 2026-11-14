"""Lectura con efemérides sintéticas (no dependen de data/): bordes, errores y semáforo."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import lectura
from app.services import lectura as service

HEADER = "utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg\n"
START = datetime(2026, 12, 1)
HOURS = 48                                   # 48 intervalos de 1 h -> 49 filas


def ephem_csv(sun_up: int, earth_up: int, rows: int = HOURS + 1) -> str:
    """Efeméride de 1 h: Sol y Tierra por encima del horizonte solo en las primeras filas."""
    lines = []
    for i in range(rows):
        t = (START + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%MZ")
        lines.append(f"{t},10,{0.4 if i < sun_up else -0.4},200,{0.4 if i < earth_up else -0.4}")
    return HEADER + "\n".join(lines) + "\n"


@pytest.fixture
def api(data_dir) -> TestClient:
    service.clear_cache()
    app = FastAPI()
    app.include_router(lectura.router)
    return TestClient(app)


def put(data_dir, name, content="", binary=None):
    path = data_dir / name
    path.write_bytes(binary if binary is not None else content.encode())
    return path


def metrics(api, *sites, **params):
    return api.get("/api/metricas", params={"site_id": list(sites), **params})


# ------------------------------------------------------------------ cálculo y semáforo
@pytest.mark.parametrize("sun_up,earth_up,pct_sol,pct_tierra,semaforo", [
    (48, 48, 100.0, 100.0, ("verde", "verde", "verde")),
    (24, 48, 50.0, 100.0, ("amarillo", "verde", "amarillo")),
    (10, 10, 20.83, 20.83, ("rojo", "rojo", "rojo")),
    (34, 0, 70.83, 0.0, ("verde", "rojo", "rojo")),
    (0, 48, 0.0, 100.0, ("rojo", "verde", "rojo")),
    (20, 30, 41.67, 62.5, ("amarillo", "amarillo", "amarillo")),
])
def test_metricas_sinteticas(api, data_dir, sun_up, earth_up, pct_sol, pct_tierra, semaforo):
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(sun_up, earth_up))
    item = metrics(api, "sitio_uno").json()["resultados"][0]
    assert (item["horas_sol"], item["horas_tierra_visible"]) == (sun_up, earth_up)
    assert (item["pct_sol"], item["pct_tierra"]) == (pct_sol, pct_tierra)
    assert (item["semaforo"]["energia"], item["semaforo"]["comunicacion"], item["semaforo"]["global"]) == semaforo
    assert item["sol_el_max_deg"] == (0.4 if sun_up else -0.4)
    assert item["sol_el_media_deg"] == (0.4 if sun_up else None)


@pytest.mark.parametrize("pct,expected", [
    (100.0, "verde"), (70.0, "verde"), (69.99, "amarillo"), (40.0, "amarillo"), (39.99, "rojo"), (0.0, "rojo"),
])
def test_frontera_del_semaforo(pct, expected):
    assert service.traffic_light(pct, pct) == {"energia": expected, "comunicacion": expected, "global": expected}


def test_semaforo_global_es_el_peor():
    assert service.traffic_light(100.0, 10.0)["global"] == "rojo"
    assert service.traffic_light(75.0, 50.0)["global"] == "amarillo"


# ------------------------------------------------------------------ metricas.csv es opcional
def reference_row(site="sitio_uno", fin="2026-12-03T00:00Z"):
    return ("site_id,inicio_utc,fin_utc,horas_sol,pct_sol,sol_el_max_deg,sol_el_media_deg,horas_tierra_visible,pct_tierra\n"
            f"{site},2026-12-01T00:00Z,{fin},48,100,0.4,0.4,48,100\n")


@pytest.mark.parametrize("name,content,binary", [
    ("vacío", "", None),
    ("corrupto", "esto,no,es\nun,csv,valido\n", None),
    ("binario", "", b"\x00\x01\x02\xff"),
    ("sitio desaparecido", reference_row(site="otro_sitio"), None),
])
def test_metricas_csv_invalido_no_bloquea(api, data_dir, name, content, binary):
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(48, 48))
    put(data_dir, "metricas.csv", content, binary)
    response = metrics(api, "sitio_uno")
    assert response.status_code == 200, name
    assert response.json()["resultados"][0]["referencia"] is None


def test_metricas_csv_ausente_no_bloquea(api, data_dir):
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(48, 48))
    assert metrics(api, "sitio_uno").json()["resultados"][0]["referencia"] is None


def test_referencia_coincide_y_detecta_diferencias(api, data_dir):
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(48, 48))
    put(data_dir, "metricas.csv", reference_row())
    ref = metrics(api, "sitio_uno", desde="2026-12-01T00:00Z", hasta="2026-12-03T00:00Z").json()["resultados"][0]["referencia"]
    assert ref["coincide"] is True
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(24, 48))
    service.clear_cache()
    ref = metrics(api, "sitio_uno", desde="2026-12-01T00:00Z", hasta="2026-12-03T00:00Z").json()["resultados"][0]["referencia"]
    assert ref["coincide"] is False


# ------------------------------------------------------------------ efemérides inválidas o ausentes
@pytest.mark.parametrize("content,binary", [
    (HEADER + "2026-12-01T00:00Z,10,nan,200,0.4\n" * 30, None),
    ("", None),
    (HEADER, None),
    ("", b"\x00\xff\x00\xff"),
    ("x" * 100, None),
])
@pytest.mark.parametrize("url", ["/api/efemerides/sitio_uno", "/api/puestas/sitio_uno", "/api/metricas?site_id=sitio_uno"])
def test_efemerides_invalidas_dan_503(api, data_dir, content, binary, url):
    put(data_dir, "efemerides_sitio_uno.csv", content, binary)
    response = api.get(url)
    assert response.status_code == 503 and "detail" in response.json()


@pytest.mark.parametrize("url", ["/api/efemerides/sitio_uno", "/api/puestas/sitio_uno", "/api/metricas?site_id=sitio_uno"])
def test_sitio_sin_archivo_de_efemerides_404(api, url):
    assert api.get(url).status_code == 404


def test_efemerides_es_un_directorio_503(api, data_dir):
    (data_dir / "efemerides_sitio_uno.csv").mkdir()
    assert api.get("/api/efemerides/sitio_uno").status_code == 503


# ------------------------------------------------------------------ site_id hostil
@pytest.mark.parametrize("site_id", ["SITIO_UNO", "..%2F..%2Fetc%2Fpasswd", "..", "sitio_uno%00", "sitio%20uno", "%3Cscript%3E"])
def test_site_id_hostil_no_llega_al_disco(api, data_dir, site_id):
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(48, 48))
    for url in (f"/api/efemerides/{site_id}", f"/api/puestas/{site_id}"):
        response = api.get(url)
        assert response.status_code in (404, 422)
        assert "etc" not in response.text and "script" not in response.text.lower()


# ------------------------------------------------------------------ fechas
@pytest.fixture
def loaded(api, data_dir):
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(24, 48))
    return api


def test_desde_sin_hasta_usa_el_final_de_los_datos(loaded):
    body = loaded.get("/api/efemerides/sitio_uno", params={"desde": "2026-12-02T00:00Z"}).json()
    assert body["inicio_utc"] == "2026-12-02T00:00Z" and body["fin_utc"] == "2026-12-03T00:00Z"
    assert len(body["filas"]) == 25


@pytest.mark.parametrize("value", ["2026-12-01T00:00+01:00", "2026-12-01T00:00:30Z", "2026-12-01", "2026-12-01T00:00:00Z", ""])
def test_fechas_con_zona_o_segundos_son_422(loaded, value):
    assert loaded.get("/api/efemerides/sitio_uno", params={"desde": value}).status_code == 422
    assert loaded.get("/api/puestas/sitio_uno", params={"desde": value}).status_code == 422


def test_desde_en_el_ultimo_instante(loaded):
    last = "2026-12-03T00:00Z"
    assert loaded.get("/api/efemerides/sitio_uno", params={"desde": last}).status_code == 422   # no queda rango
    puestas = loaded.get("/api/puestas/sitio_uno", params={"desde": last})
    assert puestas.status_code == 200 and puestas.json()["sol"]["horas_restantes"] is None


def test_parametro_desde_repetido_usa_el_ultimo(loaded):
    response = loaded.get("/api/puestas/sitio_uno?desde=2026-12-01T00:00Z&desde=2026-12-02T00:00Z")
    assert response.status_code == 200 and response.json()["desde_utc"] == "2026-12-02T00:00Z"


def test_site_id_vacio_en_metricas(loaded):
    assert loaded.get("/api/metricas?site_id=").status_code in (404, 422)


def test_desde_fuera_de_la_rejilla_no_tiene_referencia(api, data_dir):
    put(data_dir, "efemerides_sitio_uno.csv", ephem_csv(48, 48))
    put(data_dir, "metricas.csv", reference_row())
    item = metrics(api, "sitio_uno", desde="2026-12-01T00:30Z", hasta="2026-12-03T00:00Z").json()["resultados"][0]
    assert item["referencia"] is None


# ------------------------------------------------------------------ flujo real: cargar y leer
def test_subida_por_api_se_ve_en_la_lectura(data_dir, monkeypatch):
    from app.main import app
    from tests.conftest import API_KEY, upload
    service.clear_cache()
    client = TestClient(app)
    assert client.get("/api/efemerides/sitio_uno").status_code == 404
    content = ephem_csv(48, 48).encode()
    assert upload(client, "efemerides", content, "efemerides_sitio_uno.csv", dry_run=False).status_code == 200
    assert len(client.get("/api/efemerides/sitio_uno").json()["filas"]) == 49
    content = ephem_csv(10, 10, rows=30).encode()
    assert upload(client, "efemerides", content, "efemerides_sitio_uno.csv", dry_run=False).status_code == 200
    assert len(client.get("/api/efemerides/sitio_uno").json()["filas"]) == 30

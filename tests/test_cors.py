"""CORS: orígenes configurables, métodos y cabeceras acotados, sin credenciales."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.core.cors import add_cors
from app.core.limits import upload_guard
from tests.conftest import API_KEY

LOCAL = "http://localhost:5173"
PAGES = "https://apogeo-navigator.pages.dev"
PROD = "https://navigator.apogeo.example"


def build(monkeypatch, origins=None, regex=None) -> TestClient:
    for name, value in (("CORS_ORIGINS", origins), ("CORS_ORIGIN_REGEX", regex)):
        monkeypatch.delenv(name, raising=False) if value is None else monkeypatch.setenv(name, value)
    app = FastAPI()
    app.middleware("http")(upload_guard)
    add_cors(app)

    @app.get("/api/x")
    def x():
        return {"ok": True}

    @app.post("/api/datos/sites")
    def datos():
        return {"ok": True}
    return TestClient(app)


def preflight(client, origin, method="POST", headers="X-API-Key, Content-Type"):
    return client.options("/api/datos/sites", headers={"Origin": origin, "Access-Control-Request-Method": method,
                                                       "Access-Control-Request-Headers": headers})


def test_por_defecto_solo_se_permite_localhost(monkeypatch):
    c = build(monkeypatch)
    assert c.get("/api/x", headers={"Origin": LOCAL}).headers["access-control-allow-origin"] == LOCAL
    assert "access-control-allow-origin" not in c.get("/api/x", headers={"Origin": "https://malo.example"}).headers


def test_origenes_configurados_por_variable(monkeypatch):
    c = build(monkeypatch, origins=f"{LOCAL}, {PROD}/ ,")
    assert c.get("/api/x", headers={"Origin": PROD}).headers["access-control-allow-origin"] == PROD
    assert "access-control-allow-origin" not in c.get("/api/x", headers={"Origin": PAGES}).headers


def test_regex_de_previsualizaciones_de_pages(monkeypatch):
    c = build(monkeypatch, origins=LOCAL, regex=r"^https://([a-z0-9-]+\.)?apogeo-navigator\.pages\.dev$")
    for ok in (PAGES, "https://abc123.apogeo-navigator.pages.dev"):
        assert c.get("/api/x", headers={"Origin": ok}).headers["access-control-allow-origin"] == ok
    for mal in ("https://apogeo-navigator.pages.dev.evil.example", "https://otro.pages.dev", "http://apogeo-navigator.pages.dev"):
        assert "access-control-allow-origin" not in c.get("/api/x", headers={"Origin": mal}).headers


def test_el_comodin_no_se_admite(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ValueError):
        config.get_cors_origins()


def test_preflight_permite_los_metodos_y_cabeceras_acordados(monkeypatch):
    r = preflight(build(monkeypatch), LOCAL)
    assert r.status_code == 200
    assert set(r.headers["access-control-allow-methods"].split(", ")) == {"GET", "POST", "OPTIONS"}
    permitidas = {h.strip().lower() for h in r.headers["access-control-allow-headers"].split(",")}
    assert {"x-api-key", "content-type"} <= permitidas
    assert r.headers["access-control-max-age"] == "600"


@pytest.mark.parametrize("metodo", ["DELETE", "PUT", "PATCH"])
def test_preflight_rechaza_otros_metodos(monkeypatch, metodo):
    assert preflight(build(monkeypatch), LOCAL, method=metodo).status_code == 400


def test_preflight_rechaza_otras_cabeceras(monkeypatch):
    assert preflight(build(monkeypatch), LOCAL, headers="Authorization").status_code == 400


def test_preflight_de_origen_no_permitido_no_concede_nada(monkeypatch):
    r = preflight(build(monkeypatch), "https://malo.example")
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers


def test_nunca_hay_credenciales(monkeypatch):
    c = build(monkeypatch)
    assert "access-control-allow-credentials" not in c.get("/api/x", headers={"Origin": LOCAL}).headers
    assert "access-control-allow-credentials" not in preflight(c, LOCAL).headers
    assert config.CORS_ALLOW_CREDENTIALS is False


def test_la_respuesta_401_del_guardia_lleva_cabeceras_cors(monkeypatch):
    monkeypatch.setenv("UPLOAD_API_KEY", API_KEY)
    r = build(monkeypatch).post("/api/datos/sites", headers={"Origin": LOCAL})            # sin API key
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == LOCAL


def test_la_respuesta_413_del_guardia_lleva_cabeceras_cors(monkeypatch):
    """Con UPLOAD_API_KEY definida y la clave correcta, el guardia mira Content-Length y responde 413 con CORS."""
    monkeypatch.setenv("UPLOAD_API_KEY", API_KEY)
    c = build(monkeypatch)
    grande = c.post("/api/datos/sites", headers={"Origin": LOCAL, "X-API-Key": API_KEY},
                    files={"file": ("sites.csv", b"a" * (config.MAX_FILE_BYTES + config.MULTIPART_OVERHEAD_BYTES + 10))})
    assert grande.status_code == 413
    assert grande.json()["codigo"] == "E_TAMANO"
    assert grande.headers["access-control-allow-origin"] == LOCAL


def test_un_archivo_dentro_del_limite_no_recibe_413(monkeypatch):
    monkeypatch.setenv("UPLOAD_API_KEY", API_KEY)
    c = build(monkeypatch)
    ok = c.post("/api/datos/sites", headers={"Origin": LOCAL, "X-API-Key": API_KEY},
                files={"file": ("sites.csv", b"a" * 1024)})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == LOCAL


def test_la_aplicacion_real_tiene_cors(monkeypatch):
    from app.main import app
    nombres = [m.cls.__name__ for m in app.user_middleware]
    assert nombres.index("CORSMiddleware") < nombres.index("BaseHTTPMiddleware")      # primero en la lista = más externo

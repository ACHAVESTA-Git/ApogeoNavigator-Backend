"""Cabeceras de seguridad HTTP: en todas las respuestas, HSTS solo en producción, no-store en las rutas sensibles."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import config as c
from app.core.peticion import RequestContextMiddleware
from app.core.seguridad_http import SecurityHeadersMiddleware, _sin_cache


@pytest.fixture
def client(monkeypatch):
    for var in (c.ENV_CLOUD_RUN_MARKER, c.ENV_ENVIRONMENT):
        monkeypatch.delenv(var, raising=False)
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/sites")
    def sites():
        return []

    @app.get("/api/admin/claves")
    def admin():
        return []

    @app.post("/api/datos/sites")
    def datos():
        return {}

    @app.get("/api/auth/login")
    def auth():
        return {}

    @app.get("/api/datosfalsos")
    def parecida():
        return {}

    @app.get("/api/prohibido")
    def prohibido():
        raise HTTPException(403)

    @app.get("/api/falla")
    def falla():
        raise RuntimeError("imprevisto")
    return TestClient(app, raise_server_exceptions=False)


def _fijas(r):
    for nombre, valor in c.SECURITY_HEADERS:
        assert r.headers[nombre] == valor


def test_las_cabeceras_fijas_salen_en_toda_respuesta(client):
    for r in (client.get("/api/sites"), client.get("/api/prohibido"), client.get("/api/no-existe"), client.get("/api/falla")):
        _fijas(r)
    assert [r.status_code for r in (client.get("/api/prohibido"), client.get("/api/falla"))] == [403, 500]


def test_valores_pedidos(client):
    r = client.get("/api/sites")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in r.headers      # Swagger necesita sus recursos


def test_hsts_solo_en_produccion(client, monkeypatch):
    assert c.HSTS_HEADER not in client.get("/api/sites").headers
    monkeypatch.setenv(c.ENV_ENVIRONMENT, c.ENVIRONMENT_PRODUCTION)
    assert client.get("/api/sites").headers[c.HSTS_HEADER] == "max-age=31536000; includeSubDomains"
    monkeypatch.delenv(c.ENV_ENVIRONMENT)
    monkeypatch.setenv(c.ENV_CLOUD_RUN_MARKER, "apogeo-backend")
    assert c.HSTS_HEADER in client.get("/api/sites").headers


@pytest.mark.parametrize("metodo,ruta", [("get", "/api/admin/claves"), ("post", "/api/datos/sites"), ("get", "/api/auth/login")])
def test_no_store_en_rutas_sensibles(client, metodo, ruta):
    assert getattr(client, metodo)(ruta).headers["cache-control"] == "no-store"


def test_no_store_no_se_aplica_al_resto(client):
    assert "cache-control" not in client.get("/api/sites").headers
    assert "cache-control" not in client.get("/api/datosfalsos").headers      # parecida a /api/datos, pero no es esa ruta


def test_sin_cache_distingue_prefijos():
    assert _sin_cache("/api/admin") and _sin_cache("/api/admin/claves/x/revocar") and _sin_cache("/api/datos/validar/sites")
    assert not _sin_cache("/api/administrador") and not _sin_cache("/api/sites") and not _sin_cache("/docs")


def test_la_aplicacion_real_las_lleva_tambien_en_rechazos_y_docs(monkeypatch):
    from app.main import app
    monkeypatch.delenv(c.ENV_CLOUD_RUN_MARKER, raising=False)
    monkeypatch.delenv(c.ENV_ENVIRONMENT, raising=False)
    real = TestClient(app, raise_server_exceptions=False)
    sin_clave = real.get("/api/admin/claves")          # 401 del gestor de claves
    assert sin_clave.status_code == 401
    _fijas(sin_clave)
    assert sin_clave.headers["cache-control"] == "no-store"
    docs = real.get("/docs")
    assert docs.status_code == 200
    _fijas(docs)
    assert "content-security-policy" not in docs.headers
    assert "cache-control" not in docs.headers

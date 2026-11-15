"""B4: DTO de health, clave pública y errores de la carga, y su documentación en OpenAPI."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core import config as c
from app.main import app
from app.schemas.common import ErrorOut
from app.schemas.report import ErrorValidacionOut, Report
from app.schemas.seguridad import ClavePublicaOut, HealthOut
from app.security import pqc
from tests.conftest import API_KEY, fixture_bytes, upload

OPENAPI = app.openapi()


def _ref(ruta, metodo, codigo):
    contenido = OPENAPI["paths"][ruta][metodo]["responses"][codigo].get("content", {})
    return contenido.get("application/json", {}).get("schema", {}).get("$ref", "").rsplit("/", 1)[-1]


@pytest.fixture
def api(bd, data_dir):
    return TestClient(app)


# ------------------------------------------------------------------ health y clave pública
def test_health_tiene_response_model_y_el_json_de_siempre(api):
    assert _ref("/api/health", "get", "200") == "HealthOut"
    r = api.get("/api/health")
    assert r.json() == {"status": "ok"} and HealthOut.model_validate(r.json()).status == "ok"


def test_clave_publica_tiene_response_model_y_las_mismas_claves(api):
    assert _ref("/api/seguridad/clave-publica", "get", "200") == "ClavePublicaOut"
    r = api.get("/api/seguridad/clave-publica")
    assert list(r.json()) == ["algoritmo", "estandar", "clave_publica_b64", "huella_sha3_256"]
    dto = ClavePublicaOut.model_validate(r.json())
    assert dto.algoritmo == pqc.ALGORITMO and dto.estandar == pqc.ESTANDAR
    assert dto.huella_sha3_256 == hashlib.sha3_256(pqc.clave_publica()).hexdigest()


def test_clave_publica_sigue_dando_503_sin_claves_en_produccion(api, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "x")
    monkeypatch.delenv("PQC_PRIVATE_KEY")
    monkeypatch.delenv("PQC_PUBLIC_KEY")
    pqc.reiniciar()
    r = api.get("/api/seguridad/clave-publica")
    assert (r.status_code, r.json()) == (503, {"detail": c.MSG_SIGNING_UNAVAILABLE})


# ------------------------------------------------------------------ manifiesto: documentado, no modelado
def test_el_manifiesto_se_documenta_como_json_sin_response_model():
    resp = OPENAPI["paths"]["/api/seguridad/manifiesto"]["get"]["responses"]["200"]
    assert "application/json" in resp["content"] and "signature" in resp["description"]
    assert not resp["content"]["application/json"].get("schema", {}).get("$ref")      # bytes exactos, no un modelo


def test_el_manifiesto_devuelve_los_bytes_exactos(api, data_dir):
    upload(api, "sites", fixture_bytes("sites_limites.csv"), "sites.csv", dry_run=False)
    assert api.get("/api/seguridad/manifiesto").content == (data_dir / pqc.MANIFEST_FILENAME).read_bytes()


# ------------------------------------------------------------------ errores de la carga
def test_el_422_de_la_carga_se_documenta_con_su_modelo():
    for ruta, metodo in (("/api/datos/validar/{tipo}", "post"), ("/api/datos/{tipo}", "post")):
        assert _ref(ruta, metodo, "422") == "ErrorValidacionOut"
        assert _ref(ruta, metodo, "415") == "ErrorValidacionOut"
        assert _ref(ruta, metodo, "401") == _ref(ruta, metodo, "413") == "ErrorOut"


def test_el_cuerpo_real_del_422_cumple_el_modelo_documentado(api):
    r = upload(api, "sites", fixture_bytes("rango.csv"), "sites.csv")
    assert r.status_code == 422
    dto = ErrorValidacionOut.model_validate(r.json())
    assert isinstance(dto.detail, Report) and dto.detail.valido is False and dto.detail.total_errores == 6


def test_el_cuerpo_real_del_415_cumple_el_modelo_documentado(api):
    r = upload(api, "sites", b"x", "sites.txt")
    assert r.status_code == 415 and ErrorValidacionOut.model_validate(r.json()).detail.errores[0].codigo == "E_TIPO_ARCHIVO"


def test_los_cuerpos_reales_del_401_y_del_413_cumplen_errorout(api):
    sin_clave = api.post("/api/datos/validar/sites", files={"file": ("sites.csv", b"x")})
    grande = api.post("/api/datos/validar/sites", headers={"X-API-Key": API_KEY},
                      files={"file": ("sites.csv", b"a" * (c.MAX_FILE_BYTES + c.MULTIPART_OVERHEAD_BYTES + 10))})
    assert (sin_clave.status_code, grande.status_code) == (401, 413)
    assert isinstance(ErrorOut.model_validate(sin_clave.json()).detail, str) and isinstance(ErrorOut.model_validate(grande.json()).detail, str)


def test_el_guardado_correcto_sigue_siendo_savresult(api):
    r = upload(api, "sites", fixture_bytes("sites_limites.csv"), "sites.csv", dry_run=False)
    assert r.status_code == 200 and {"guardado", "copia_historial"} <= set(r.json())

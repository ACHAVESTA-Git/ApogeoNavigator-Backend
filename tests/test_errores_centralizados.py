"""Manejadores de error centralizados y DTO comunes (ErrorOut, Page, UtcStr)."""
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import DisconnectionError, IntegrityError, InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core import config as c
from app.core.errors import register_handlers
from app.main import app as real_app
from app.schemas.common import ErrorOut, Page, UtcStr
from app.services import datastore
from app.services.blobstore import StoreError
from app.services.lectura import LecturaError
from tests.conftest import API_KEY

SQL_SECRETO = "SELECT clave_secreta FROM usuarios WHERE id = :id"


def _db(exc_cls):
    return exc_cls(SQL_SECRETO, {"id": "valor-secreto"}, Exception("conexión rechazada en 10.0.0.5"))


def _lanzar(exc):
    def ruta():
        raise exc
    return ruta


@pytest.fixture
def mini():
    """Aplicación mínima con los manejadores y una ruta por excepción."""
    app = FastAPI()
    register_handlers(app)
    rutas = {
        "/lectura-404": LecturaError(404, "El sitio no existe"), "/lectura-422": LecturaError(422, "rango inválido"),
        "/bucket": StoreError("GoogleAPICallError secreto"),
        "/db-operational": _db(OperationalError), "/db-interface": _db(InterfaceError), "/db-disconnect": DisconnectionError("x"),
        "/db-pool": PoolTimeoutError("QueuePool limit"), "/db-programming": _db(ProgrammingError), "/db-integrity": _db(IntegrityError),
        "/boom": RuntimeError("detalle interno secreto"), "/http-418": HTTPException(418, "soy una tetera"),
    }
    for ruta, exc in rutas.items():
        app.add_api_route(ruta, _lanzar(exc))

    @app.get("/entero")
    def entero(n: int):
        return {"n": n}
    return TestClient(app, raise_server_exceptions=False)


# ------------------------------------------------------------------ cada manejador
def test_lecturaerror_se_convierte_en_json_con_su_codigo(mini):
    r = mini.get("/lectura-404")
    assert (r.status_code, r.json()) == (404, {"detail": "El sitio no existe"})
    assert mini.get("/lectura-422").json() == {"detail": "rango inválido"}


def test_storeerror_es_503_con_el_texto_del_bucket(mini):
    r = mini.get("/bucket")
    assert (r.status_code, r.json()) == (503, {"detail": c.MSG_BUCKET_UNAVAILABLE})
    assert "secreto" not in r.text


@pytest.mark.parametrize("ruta", ["/db-operational", "/db-interface", "/db-disconnect", "/db-pool"])
def test_caida_de_la_base_de_datos_es_503_sin_filtrar_sql_ni_parametros(mini, ruta):
    r = mini.get(ruta)
    assert (r.status_code, r.json()) == (503, {"detail": c.MSG_DB_UNAVAILABLE})
    assert r.headers["content-type"].startswith("application/json")
    for filtrado in ("SELECT", "secreto", "10.0.0.5"):
        assert filtrado not in r.text


@pytest.mark.parametrize("ruta", ["/db-programming", "/db-integrity"])
def test_otros_errores_de_sqlalchemy_son_500_genericos_no_503(mini, ruta):
    r = mini.get(ruta)
    assert (r.status_code, r.json()) == (500, {"detail": c.MSG_INTERNAL_ERROR})
    assert "SELECT" not in r.text and "secreto" not in r.text


def test_excepcion_imprevista_es_500_json_y_no_texto_plano(mini):
    r = mini.get("/boom")
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": c.MSG_INTERNAL_ERROR} and "secreto" not in r.text


def test_la_traza_va_al_log_y_no_al_cliente(mini, caplog):
    with caplog.at_level(logging.ERROR, logger=c.LOGGER_ERRORS):
        r = mini.get("/boom")
    assert "detalle interno secreto" not in r.text
    registro = next(rec for rec in caplog.records if rec.name == c.LOGGER_ERRORS)
    assert "GET /boom" in registro.getMessage() and registro.exc_info and "detalle interno secreto" in str(registro.exc_info[1])


def test_el_log_de_la_base_no_lleva_sql_ni_parametros(mini, caplog):
    with caplog.at_level(logging.ERROR, logger=c.LOGGER_ERRORS):
        mini.get("/db-operational")
    assert "OperationalError" in caplog.text and "SELECT" not in caplog.text and "valor-secreto" not in caplog.text


def test_no_se_tocan_httpexception_ni_la_validacion_nativa(mini):
    r = mini.get("/http-418")
    assert (r.status_code, r.json()) == (418, {"detail": "soy una tetera"})
    v = mini.get("/entero", params={"n": "abc"})
    assert v.status_code == 422 and isinstance(v.json()["detail"], list)          # el 422 nativo sigue siendo lista


# ------------------------------------------------------------------ con la aplicación real
@pytest.fixture
def real(bd, data_dir):
    client = TestClient(real_app, raise_server_exceptions=False)
    yield client
    real_app.dependency_overrides.clear()


def test_caida_de_la_base_en_la_api_real_es_503_json(real):
    from app.db.session import get_db

    def db_caida():
        raise _db(OperationalError)
        yield
    real_app.dependency_overrides[get_db] = db_caida
    r = real.get("/api/auditoria", headers={"X-API-Key": API_KEY})
    assert (r.status_code, r.json()) == (503, {"detail": c.MSG_DB_UNAVAILABLE})
    r = real.get("/api/auditoria/verificar", headers={"X-API-Key": API_KEY})
    assert r.status_code == 503


def test_bucket_caido_en_una_lectura_de_la_api_real_es_503_json(real):
    real_app.dependency_overrides[datastore.refresh_if_stale] = _lanzar(StoreError("caído"))
    for ruta in ("/api/sites", "/api/efemerides/sitio_uno", "/api/seguridad/manifiesto"):
        r = real.get(ruta)
        assert (r.status_code, r.json()) == (503, {"detail": c.MSG_BUCKET_UNAVAILABLE}), ruta


def test_error_imprevisto_en_la_api_real_es_500_json(real):
    real_app.dependency_overrides[datastore.refresh_if_stale] = _lanzar(RuntimeError("fallo imprevisto"))
    r = real.get("/api/sites")
    assert (r.status_code, r.json()) == (500, {"detail": c.MSG_INTERNAL_ERROR})


def test_la_lectura_real_sigue_dando_su_404_de_texto(real):
    r = real.get("/api/efemerides/no_existe")
    assert r.status_code == 404 and r.json() == {"detail": c.MSG_SITE_NOT_IN_CATALOG}


def test_el_router_de_lectura_ya_no_traduce_errores_a_mano():
    from app.routers import lectura
    assert not hasattr(lectura, "_run") and not hasattr(lectura, "run_service")


# ------------------------------------------------------------------ DTO comunes
def test_page_serializa_las_mismas_claves_que_las_paginas_antiguas():
    assert Page[int](total=2, limite=10, offset=0, items=[1, 2]).model_dump() == {"total": 2, "limite": 10, "offset": 0, "items": [1, 2]}
    assert list(Page[int](total=0, limite=1, offset=0, items=[]).model_dump()) == ["total", "limite", "offset", "items"]


def test_page_valida_el_tipo_de_los_elementos():
    with pytest.raises(ValidationError):
        Page[int](total=1, limite=1, offset=0, items=["no es un entero"])


def test_las_paginas_de_auditoria_y_claves_son_page():
    from app.routers.admin_claves import ClaveOut, PaginaClaves
    from app.schemas.auditoria import EventoOut, PaginaAuditoria
    assert PaginaAuditoria is Page[EventoOut] and PaginaClaves is Page[ClaveOut]


@pytest.mark.parametrize("detalle", ["texto", ["lista", {"x": 1}], {"archivo": "x", "valido": False}])
def test_errorout_documenta_las_tres_formas_de_detail(detalle):
    assert ErrorOut(detail=detalle).detail == detalle


@pytest.mark.parametrize("valor,valido", [
    ("2026-12-01T00:00Z", True), ("2027-06-01T23:59Z", True),
    ("2026-12-01T00:00:00Z", False), ("2026-12-01T00:00", False), ("2026-12-01T00:00+01:00", False),
    ("2026-12-01T00:00Z\n", False), ("x2026-12-01T00:00Z", False), ("", False),
])
def test_utcstr_acepta_solo_el_formato_del_contrato(valor, valido):
    adaptador = TypeAdapter(UtcStr)
    if valido:
        assert adaptador.validate_python(valor) == valor
    else:
        with pytest.raises(ValidationError):
            adaptador.validate_python(valor)

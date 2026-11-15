"""Respuestas HTTP estandarizadas: objeto `error`, X-Request-ID, logs legibles y nunca una traza en la respuesta."""
import json
import logging
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.core import config as c
from app.core import mensajes
from app.core.errors import register_handlers
from app.core.logging_config import configurar_logging, linea_acceso, nivel_para
from app.core.peticion import RequestContextMiddleware
from app.core.respuestas import nuevo_request_id, sanear_request_id
from app.main import app as real_app
from app.services import datastore
from app.services.blobstore import StoreError
from tests.conftest import API_KEY, fixture_bytes, upload

ERROR_KEYS = {"codigo", "estado", "titulo", "mensaje", "accion", "request_id", "ts_utc"}
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
RID_RE = re.compile(r"^[0-9a-f]{10}$")
ESTADOS_BASE = [400, 401, 403, 404, 405, 409, 413, 415, 422, 429, 500, 502, 503, 504]
PROHIBIDO_EN_RESPUESTAS = ("Traceback", 'File "', ".py", "/Users/", "/srv/", "hunter2", "SELECT", "password", "secreto-interno", "Errno")


def _verificar_error(r, estado, codigo=None):
    cuerpo = r.json()
    assert r.status_code == estado and set(cuerpo["error"]) == ERROR_KEYS
    e = cuerpo["error"]
    assert e["estado"] == estado and TS_RE.match(e["ts_utc"]) and RID_RE.match(e["request_id"])
    assert e["request_id"] == r.headers["x-request-id"]
    assert e["titulo"] and len(e["titulo"]) <= 40 and e["mensaje"] and e["accion"]
    if codigo:
        assert e["codigo"] == codigo
    return e


@pytest.fixture
def mini():
    """App mínima con middleware y manejadores y una ruta por estado del catálogo base."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_handlers(app)
    for estado in ESTADOS_BASE:
        app.add_api_route(f"/e/{estado}", (lambda e=estado: (_ for _ in ()).throw(HTTPException(e, f"detalle {e}"))), methods=["GET"])

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/entero")
    def entero(n: int, limite: int = 5):
        return {"n": n}
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def real(bd, data_dir):
    client = TestClient(real_app, raise_server_exceptions=False)
    yield client
    real_app.dependency_overrides.clear()


# ------------------------------------------------------------------ el catálogo
def test_el_catalogo_base_cubre_todos_los_estados_pedidos():
    assert sorted(mensajes.BASE) == ESTADOS_BASE


def test_cada_entrada_del_catalogo_esta_completa_y_es_coherente():
    vistos = set()
    for codigo, m in mensajes.CODIGOS.items():
        assert codigo == m.codigo and codigo.isupper() and codigo not in vistos
        vistos.add(codigo)
        assert 0 < len(m.titulo) <= 40 and m.mensaje and m.accion and m.estado in mensajes.BASE
        datos = mensajes.construir(codigo, {"sitio": "x", "detalle": "d", "resumen": "r"})
        assert set(datos) == {"codigo", "estado", "titulo", "mensaje", "accion"} and datos["mensaje"]


def test_construir_no_falla_si_faltan_variables_del_contexto():
    assert "«(no válido)»" in mensajes.construir("SITIO_NO_ENCONTRADO")["mensaje"]
    assert mensajes.construir("CSV_INVALIDO")["mensaje"]                   # sin resumen cae en un texto razonable
    assert mensajes.construir("DATOS_INVALIDOS")["mensaje"]


@pytest.mark.parametrize("estado,esperado", [(418, "PETICION_INVALIDA"), (451, "PETICION_INVALIDA"), (507, "ERROR_INTERNO"), (599, "ERROR_INTERNO")])
def test_un_estado_fuera_del_catalogo_cae_en_la_entrada_de_su_familia(estado, esperado):
    assert mensajes.codigo_para(estado, "x") == esperado


@pytest.mark.parametrize("estado,detalle,codigo", [
    (404, c.MSG_SITE_NOT_IN_CATALOG, "SITIO_NO_ENCONTRADO"), (404, c.MSG_NO_EPHEMERIS, "EFEMERIDES_NO_ENCONTRADAS"),
    (404, c.MSG_NO_MANIFEST, "MANIFIESTO_NO_GENERADO"), (404, "Tipo desconocido; usa uno de ['sites']", "TIPO_DATOS_DESCONOCIDO"),
    (404, c.MSG_KEYS_NO_USER, "USUARIO_NO_ENCONTRADO"), (404, c.MSG_KEYS_NO_KEY, "CLAVE_NO_ENCONTRADA"),
    (401, c.MSG_API_KEY_INVALID, "CLAVE_INVALIDA"), (401, c.MSG_KEYS_ADMIN_KEY, "CLAVE_INVALIDA"),
    (422, c.MSG_RANGE_ORDER, "RANGO_FECHAS_INVALIDO"), (422, c.MSG_AUDIT_RANGE_ORDER, "RANGO_FECHAS_INVALIDO"),
    (422, "'desde': Formato esperado: YYYY-MM-DDTHH:MMZ (UTC)", "RANGO_FECHAS_INVALIDO"),
    (422, c.MSG_OUT_OF_DATA.format(param="hasta"), "RANGO_FECHAS_INVALIDO"),
    (422, c.MSG_QUERY_RANGE.format(max=35, days=90.0), "RANGO_DEMASIADO_LARGO"),
    (503, c.MSG_NO_SITES, "DATOS_NO_DISPONIBLES"), (503, c.MSG_SITES_INVALID, "DATOS_NO_DISPONIBLES"),
    (503, c.MSG_DB_UNAVAILABLE, "BD_NO_DISPONIBLE"), (503, c.MSG_DB_DISABLED, "BD_NO_DISPONIBLE"),
    (503, c.MSG_BUCKET_UNAVAILABLE, "ALMACEN_NO_DISPONIBLE"), (503, c.MSG_SIGNING_UNAVAILABLE, "FIRMA_NO_DISPONIBLE"),
    (409, c.MSG_KEYS_ALREADY_REVOKED, "CLAVE_YA_REVOCADA"), (409, c.MSG_KEYS_INACTIVE_USER, "USUARIO_INACTIVO"),
    (409, c.MSG_KEYS_ALREADY_ACTIVE_GENERIC, "CLAVE_DUPLICADA"), (409, c.MSG_KEYS_ALREADY_ACTIVE.format(tipo="firma"), "CLAVE_DUPLICADA"),
])
def test_cada_texto_conocido_se_traduce_a_su_codigo_de_dominio(estado, detalle, codigo):
    assert mensajes.codigo_para(estado, detalle) == codigo


def test_un_codigo_de_dominio_no_se_usa_con_un_estado_que_no_es_el_suyo():
    assert mensajes.codigo_para(500, c.MSG_SITE_NOT_IN_CATALOG) == "ERROR_INTERNO"
    assert mensajes.codigo_para(422, c.MSG_NO_SITES) == "DATOS_INVALIDOS"


def test_todos_los_codigos_de_dominio_tienen_algun_texto_que_los_produce():
    producidos = set(mensajes._POR_DETALLE.values()) | {cod for _, cod in mensajes._POR_PATRON} | {c.CODE_CSV_INVALID}
    assert set(mensajes.DOMINIO) <= producidos


# ------------------------------------------------------------------ un estado a la vez
@pytest.mark.parametrize("estado", ESTADOS_BASE)
def test_cada_estado_del_catalogo_responde_con_su_objeto_error(mini, estado):
    r = mini.get(f"/e/{estado}")
    e = _verificar_error(r, estado, mensajes.BASE[estado].codigo)
    assert r.json()["detail"] == f"detalle {estado}"                      # detail intacto
    assert r.headers["content-type"].startswith("application/json")


def test_el_404_de_ruta_inexistente_y_el_405_se_estandarizan(mini):
    r = mini.get("/no-existe")
    assert r.json()["detail"] == "Not Found"
    _verificar_error(r, 404, "NO_ENCONTRADO")
    r = mini.post("/ok")
    assert r.json()["detail"] == "Method Not Allowed" and "GET" in r.headers["allow"]
    _verificar_error(r, 405, "METODO_NO_PERMITIDO")


def test_el_422_de_validacion_conserva_su_lista_y_anade_error(mini):
    r = mini.get("/entero", params={"n": "valor-secreto-12345", "limite": "x"})
    assert isinstance(r.json()["detail"], list) and r.json()["detail"][0]["loc"]
    e = _verificar_error(r, 422, "DATOS_INVALIDOS")
    assert "n" in e["mensaje"] and "limite" in e["mensaje"] and "valor-secreto-12345" not in e["mensaje"]


def test_500_imprevisto_no_deja_pasar_nada_interno(mini):
    mini.app.add_api_route("/boom", lambda: (_ for _ in ()).throw(RuntimeError("secreto-interno")), methods=["GET"])
    r = mini.get("/boom")
    e = _verificar_error(r, 500, "ERROR_INTERNO")
    assert r.json()["detail"] == c.MSG_INTERNAL_ERROR and "secreto-interno" not in r.text


# ------------------------------------------------------------------ con la aplicación real: códigos de dominio
def test_sitio_inexistente_cita_el_identificador_seguro(real):
    r = real.get("/api/efemerides/shakleton")
    e = _verificar_error(r, 404, "SITIO_NO_ENCONTRADO")
    assert "«shakleton»" in e["mensaje"] and r.json()["detail"] == c.MSG_SITE_NOT_IN_CATALOG


@pytest.mark.parametrize("hostil", ["<script>alert(1)</script>", "..%2F..%2Fetc%2Fpasswd", "a b", "x" * 200])
def test_un_identificador_hostil_no_se_refleja_en_el_mensaje(real, hostil):
    r = real.get(f"/api/puestas/{hostil}")
    assert r.status_code in (404, 422)
    if r.status_code == 404:
        texto = json.dumps(r.json()["error"], ensure_ascii=False)       # (si la ruta ni casa, es el 404 genérico)
        assert "script" not in texto and "etc" not in texto and "xxxxx" not in texto
        assert r.json()["error"]["codigo"] in ("NO_ENCONTRADO", "SITIO_NO_ENCONTRADO")


@pytest.mark.parametrize("hostil", ["a%20b", "%3Cb%3E", "x%00y", "sitio.uno", "SITIO%27%3B"])
def test_un_identificador_sin_barras_pero_no_valido_se_sustituye_en_el_mensaje(real, hostil):
    r = real.get(f"/api/puestas/{hostil}")
    assert r.status_code == 404 and "«(no válido)»" in r.json()["error"]["mensaje"]
    assert hostil not in r.text and "<b>" not in r.text


def test_sitio_sin_efemerides(real):
    r = real.get("/api/efemerides/sitio_dos")
    assert "«sitio_dos»" in _verificar_error(r, 404, "EFEMERIDES_NO_ENCONTRADAS")["mensaje"]


@pytest.mark.parametrize("params,codigo", [
    ({"desde": "hola"}, "RANGO_FECHAS_INVALIDO"),
    ({"desde": "2026-12-02T00:00Z", "hasta": "2026-12-01T00:00Z"}, "RANGO_FECHAS_INVALIDO"),
    ({"desde": "2026-12-01T00:00Z", "hasta": "2027-01-20T00:00Z"}, "RANGO_DEMASIADO_LARGO"),
])
def test_errores_de_rango(real, data_dir, params, codigo):
    from tests.conftest import make_ephem
    (data_dir / "efemerides_sitio_uno.csv").write_bytes(make_ephem(n=24 * 60))      # 60 días de datos
    r = real.get("/api/efemerides/sitio_uno", params=params)
    _verificar_error(r, 422, codigo)


def test_csv_invalido_resume_los_errores_y_mantiene_el_informe_en_detail(real):
    r = upload(real, "sites", fixture_bytes("rango.csv"), "sites.csv")
    e = _verificar_error(r, 422, "CSV_INVALIDO")
    assert "6 error(es)" in e["mensaje"] and r.json()["detail"]["total_errores"] == 6 and r.json()["detail"]["archivo"] == "sites.csv"


def test_clave_invalida_en_el_guardia_y_en_la_dependencia(real):
    sin_clave = real.post("/api/datos/validar/sites", files={"file": ("sites.csv", b"x")})
    _verificar_error(sin_clave, 401, "CLAVE_INVALIDA")
    _verificar_error(real.get("/api/auditoria"), 401, "CLAVE_INVALIDA")
    _verificar_error(real.get("/api/admin/claves"), 401, "CLAVE_INVALIDA")


def test_archivo_demasiado_grande_y_tipo_no_soportado(real):
    grande = real.post("/api/datos/validar/sites", headers={"X-API-Key": API_KEY},
                       files={"file": ("sites.csv", b"a" * (c.MAX_FILE_BYTES + c.MULTIPART_OVERHEAD_BYTES + 10))})
    _verificar_error(grande, 413, "ARCHIVO_DEMASIADO_GRANDE")
    assert grande.json()["codigo"] == c.E_TAMANO                           # la clave de antes sigue ahí
    _verificar_error(upload(real, "sites", b"x", "sites.txt"), 415, "TIPO_NO_SOPORTADO")


def test_servicios_caidos_dan_503_con_su_codigo(real, data_dir):
    from app.db.session import get_db
    real_app.dependency_overrides[get_db] = lambda: (_ for _ in ()).throw(
        OperationalError("SELECT secreto-interno", {}, Exception("hunter2")))
    _verificar_error(real.get("/api/auditoria", headers={"X-API-Key": API_KEY}), 503, "BD_NO_DISPONIBLE")
    real_app.dependency_overrides.clear()
    real_app.dependency_overrides[datastore.refresh_if_stale] = lambda: (_ for _ in ()).throw(StoreError("GoogleAPICallError"))
    _verificar_error(real.get("/api/sites"), 503, "ALMACEN_NO_DISPONIBLE")
    real_app.dependency_overrides.clear()
    (data_dir / "sites.csv").unlink()
    _verificar_error(real.get("/api/sites"), 503, "DATOS_NO_DISPONIBLES")
    _verificar_error(real.get("/api/seguridad/manifiesto"), 404, "MANIFIESTO_NO_GENERADO")


def test_tipo_de_datos_desconocido(real):
    _verificar_error(upload(real, "otro", b"x", "sites.csv"), 404, "TIPO_DATOS_DESCONOCIDO")


# ------------------------------------------------------------------ X-Request-ID
@pytest.mark.parametrize("ruta,metodo,estado", [("/api/health", "get", 200), ("/api/sites", "get", 200), ("/api/no-existe", "get", 404),
                                               ("/api/sites", "post", 405), ("/api/auditoria", "get", 401)])
def test_toda_respuesta_lleva_x_request_id_tambien_las_2xx(real, ruta, metodo, estado):
    r = getattr(real, metodo)(ruta)
    assert r.status_code == estado and RID_RE.match(r.headers["x-request-id"])


def test_el_request_id_del_cliente_se_reutiliza_si_es_seguro(real):
    r = real.get("/api/efemerides/no_existe", headers={"X-Request-ID": "mi-peticion-0042"})
    assert r.headers["x-request-id"] == "mi-peticion-0042" and r.json()["error"]["request_id"] == "mi-peticion-0042"
    assert real.get("/api/health", headers={"X-Request-ID": "abc.DEF_123-xyz"}).headers["x-request-id"] == "abc.DEF_123-xyz"


@pytest.mark.parametrize("malo", ["corto", "x" * 65, "con espacios aqui", "<script>alert(1)</script>", "../../etc/passwd", "a" * 8 + "é"])
def test_un_request_id_inseguro_se_sustituye_por_uno_nuevo(real, malo):
    r = real.get("/api/health", headers={"X-Request-ID": malo.encode("utf-8")})
    assert r.headers["x-request-id"] != malo and RID_RE.match(r.headers["x-request-id"])


def test_cada_peticion_tiene_su_propio_request_id(real):
    ids = {real.get("/api/health").headers["x-request-id"] for _ in range(20)}
    assert len(ids) == 20 and len({nuevo_request_id() for _ in range(50)}) == 50


def test_sanear_request_id_unitario():
    assert sanear_request_id("abcdefgh") == "abcdefgh" and sanear_request_id(None) != sanear_request_id(None)
    assert RID_RE.match(sanear_request_id("mal id")) and RID_RE.match(sanear_request_id(""))


# ------------------------------------------------------------------ nunca una traza en la respuesta
def _excepciones():
    return [RuntimeError("/Users/dev/app/servicios/secreto-interno.py password=hunter2 Traceback (most recent call last)"),
            ValueError("File \"/srv/app/db.py\", line 3 SELECT * FROM usuarios secreto-interno"),
            OSError(2, "Errno 2 No such file /srv/data/secreto-interno.csv"),
            ProgrammingError("SELECT clave FROM usuarios WHERE password = :p", {"p": "hunter2"}, Exception("secreto-interno")),
            OperationalError("SELECT 1 FROM secreto-interno", {"p": "hunter2"}, Exception("conexión rechazada /srv/pg.sock")),
            StoreError("secreto-interno /Users/dev/.config/gcloud/credentials.json")]


@pytest.mark.parametrize("exc", _excepciones(), ids=lambda e: type(e).__name__)
def test_nunca_se_filtra_una_traza_ni_sql_ni_rutas_con_la_app_real(real, exc):
    real_app.dependency_overrides[datastore.refresh_if_stale] = lambda: (_ for _ in ()).throw(exc)
    r = real.get("/api/sites")
    assert r.status_code in (500, 503) and r.headers["content-type"].startswith("application/json")
    visible = r.text + " ".join(f"{k}: {v}" for k, v in r.headers.items())
    for prohibido in PROHIBIDO_EN_RESPUESTAS:
        assert prohibido not in visible, f"se filtró «{prohibido}»"
    _verificar_error(r, r.status_code)


@pytest.mark.parametrize("exc", _excepciones(), ids=lambda e: type(e).__name__)
def test_nunca_se_filtra_tampoco_en_una_app_sin_el_middleware(exc):
    """Camino de ServerErrorMiddleware: solo manejadores, sin RequestContextMiddleware."""
    app = FastAPI()
    register_handlers(app)
    app.add_api_route("/x", lambda: (_ for _ in ()).throw(exc), methods=["GET"])
    r = TestClient(app, raise_server_exceptions=False).get("/x")
    assert r.headers["content-type"].startswith("application/json") and RID_RE.match(r.headers["x-request-id"])
    for prohibido in PROHIBIDO_EN_RESPUESTAS:
        assert prohibido not in r.text


def test_el_500_lleva_las_cabeceras_cors_y_x_request_id_expuesto(real):
    real_app.dependency_overrides[datastore.refresh_if_stale] = lambda: (_ for _ in ()).throw(RuntimeError("secreto-interno"))
    r = real.get("/api/sites", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 500 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-request-id" in r.headers["access-control-expose-headers"].lower()


# ------------------------------------------------------------------ logs
@pytest.fixture
def acceso(caplog):
    caplog.set_level(logging.DEBUG, logger=c.LOGGER_ROOT)
    return lambda: [rec for rec in caplog.records if rec.name == c.LOGGER_ACCESS]


@pytest.mark.parametrize("peticion,estado,nivel,codigo", [
    (lambda r: r.get("/api/health"), 200, logging.INFO, "OK"),
    (lambda r: r.get("/api/efemerides/no_existe"), 404, logging.INFO, "SITIO_NO_ENCONTRADO"),
    (lambda r: r.get("/api/auditoria"), 401, logging.INFO, "CLAVE_INVALIDA"),
    (lambda r: r.get("/api/auditoria", params={"limite": 0}, headers={"X-API-Key": API_KEY}), 422, logging.WARNING, "DATOS_INVALIDOS"),
    (lambda r: r.post("/api/sites"), 405, logging.WARNING, "METODO_NO_PERMITIDO"),
])
def test_una_linea_de_log_legible_por_peticion(real, acceso, peticion, estado, nivel, codigo):
    r = peticion(real)
    registros = [x for x in acceso() if r.headers["x-request-id"] in x.getMessage()]
    assert len(registros) == 1
    linea = registros[0].getMessage()
    assert registros[0].levelno == nivel and f"→ {estado} {codigo} (" in linea and re.search(r"\(\d+ ms\)", linea)
    assert linea.startswith(r.headers["x-request-id"])
    if estado >= 400:
        assert " | " in linea and r.json()["error"]["accion"] in linea


def test_503_y_500_se_registran_en_error_y_el_500_deja_la_traza_enlazada(real, acceso, caplog):
    real_app.dependency_overrides[datastore.refresh_if_stale] = lambda: (_ for _ in ()).throw(RuntimeError("fallo-visible-solo-en-el-log"))
    r = real.get("/api/sites")
    rid = r.headers["x-request-id"]
    assert [x.levelno for x in acceso() if rid in x.getMessage()] == [logging.ERROR]
    traza = [x for x in caplog.records if x.name == c.LOGGER_ERRORS and rid in x.getMessage()]
    assert len(traza) == 1 and traza[0].exc_info and "fallo-visible-solo-en-el-log" in str(traza[0].exc_info[1])
    assert "fallo-visible-solo-en-el-log" not in r.text


def test_el_log_no_lleva_claves_ni_query_string_ni_cabeceras(real, acceso):
    real.get("/api/metricas", params={"site_id": "shackleton", "clave": "SECRETO-EN-LA-URL"}, headers={"X-API-Key": "SECRETO-EN-LA-CABECERA"})
    texto = " ".join(x.getMessage() for x in acceso())
    assert "SECRETO" not in texto and "?" not in texto and "clave=" not in texto


def test_el_log_no_lleva_ip_ni_emails(real, acceso):
    real.get("/api/health", headers={"X-Forwarded-For": "198.51.100.77", "User-Agent": "ana@example.org"})
    texto = " ".join(x.getMessage() for x in acceso())
    assert "198.51.100.77" not in texto and "ana@example.org" not in texto


def test_niveles_segun_el_estado():
    assert [nivel_para(e) for e in (200, 204, 302, 401, 404, 400, 405, 409, 422, 500, 503)] == [
        logging.INFO, logging.INFO, logging.INFO, logging.INFO, logging.INFO, logging.WARNING, logging.WARNING, logging.WARNING,
        logging.WARNING, logging.ERROR, logging.ERROR]


def test_formato_de_la_linea():
    assert linea_acceso("9f2c1e7a4b", "GET", "/api/x", 404, "SITIO_NO_ENCONTRADO", 3.2, "Revisa el identificador") == \
        "9f2c1e7a4b GET  /api/x → 404 SITIO_NO_ENCONTRADO (3 ms) | Revisa el identificador"
    assert linea_acceso("9f2c1e7a4b", "POST", "/api/x", 200, None, 12, None) == "9f2c1e7a4b POST /api/x → 200 OK (12 ms)"


@pytest.fixture
def restaurar_logging(monkeypatch):
    yield
    monkeypatch.undo()
    configurar_logging()


def test_log_format_texto_escribe_ts_nivel_y_linea(real, monkeypatch, capsys, restaurar_logging):
    monkeypatch.setenv("LOG_FORMAT", "texto")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    configurar_logging()
    r = real.get("/api/efemerides/no_existe")
    salida = [l for l in capsys.readouterr().err.splitlines() if r.headers["x-request-id"] in l]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z INFO  [0-9a-f]{10} GET  /api/efemerides/no_existe → 404 SITIO_NO_ENCONTRADO \(\d+ ms\) \| ", salida[0])


def test_log_format_json_escribe_una_linea_json_con_los_campos(real, monkeypatch, capsys, restaurar_logging):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configurar_logging()
    r = real.get("/api/efemerides/no_existe")
    lineas = [json.loads(l) for l in capsys.readouterr().err.splitlines() if l.startswith("{")]
    registro = next(l for l in lineas if l.get("request_id") == r.headers["x-request-id"])
    assert registro["estado"] == 404 and registro["codigo"] == "SITIO_NO_ENCONTRADO" and registro["metodo"] == "GET"
    assert registro["ruta"] == "/api/efemerides/no_existe" and registro["nivel"] == "INFO" and "accion" in registro and "ms" in registro


def test_log_json_incluye_la_traza_de_los_5xx(real, monkeypatch, capsys, restaurar_logging):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configurar_logging()
    real_app.dependency_overrides[datastore.refresh_if_stale] = lambda: (_ for _ in ()).throw(RuntimeError("fallo-en-el-log"))
    r = real.get("/api/sites")
    lineas = [json.loads(l) for l in capsys.readouterr().err.splitlines() if l.startswith("{")]
    assert any("fallo-en-el-log" in l.get("traza", "") and r.headers["x-request-id"] in l["mensaje"] for l in lineas)


def test_log_level_error_oculta_las_lineas_de_info(real, monkeypatch, capsys, restaurar_logging):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    configurar_logging()
    real.get("/api/health")
    assert capsys.readouterr().err.strip() == ""


def test_un_formato_o_nivel_invalido_cae_en_el_valor_por_defecto(monkeypatch, restaurar_logging):
    monkeypatch.setenv("LOG_FORMAT", "xml")
    monkeypatch.setenv("LOG_LEVEL", "RUIDOSO")
    configurar_logging()
    assert logging.getLogger(c.LOGGER_ROOT).level == logging.INFO


def test_configurar_logging_es_idempotente(restaurar_logging):
    for _ in range(3):
        configurar_logging()
    assert len([h for h in logging.getLogger(c.LOGGER_ROOT).handlers if getattr(h, "_apogeo_handler", False)]) == 1

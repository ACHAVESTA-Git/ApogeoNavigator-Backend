"""Auditoría integrada en /api/datos, endpoints de auditoría y de seguridad, y manifiesto firmado."""
import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import config as c
from app.db import session as db_session
from app.db.enums import AccionAuditoria as A
from app.db.models import CargaArchivo, EventoAuditoria
from app.main import app
from app.security import pqc
from app.utils import auditoria_util as au
from tests.conftest import API_KEY, fixture_bytes, report_of, upload

KEY = {"X-API-Key": API_KEY}


@pytest.fixture
def api(bd, data_dir, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("IP_HASH_SALT", "sal-de-prueba")
    au.set_firmante()
    return TestClient(app)


def _events():
    with db_session.session_scope() as s:
        return s.query(EventoAuditoria).order_by(EventoAuditoria.secuencia).all()


def _cargas():
    with db_session.session_scope() as s:
        return s.query(CargaArchivo).order_by(CargaArchivo.creado_utc).all()


# ------------------------------------------------------------------ /api/datos registra auditoría
def test_validacion_valida_registra_carga_validada(api):
    assert upload(api, "sites", "sites_limites.csv", "sites.csv").status_code == 200
    (ev,), (carga,) = _events(), _cargas()
    assert ev.accion is A.CARGA_VALIDADA and ev.actor == "api-key" and ev.detalle["valido"] is True
    assert carga.valido is True and carga.sha3_256 == hashlib.sha3_256(fixture_bytes("sites_limites.csv")).hexdigest()
    assert ev.algoritmo_firma == "ML-DSA-65" and ev.huella_clave == pqc.huella_clave_publica()


def test_validacion_invalida_registra_carga_rechazada(api):
    assert upload(api, "sites", "rango.csv", "sites.csv").status_code == 422
    (ev,), (carga,) = _events(), _cargas()
    assert ev.accion is A.CARGA_RECHAZADA and carga.valido is False and carga.total_errores == 6


def test_guardado_registra_evento_y_ruta_de_historial(api, data_dir):
    assert upload(api, "sites", "sites_limites.csv", "sites.csv", dry_run=False).status_code == 200
    (ev,), (carga,) = _events(), _cargas()
    assert ev.accion is A.CARGA_GUARDADA and carga.ruta_almacenamiento == "sites.csv"
    assert carga.ruta_historial and (data_dir / "_historial" / carga.ruta_historial).is_file()


def test_guardado_invalido_registra_rechazo_y_no_escribe(api, data_dir):
    antes = (data_dir / "sites.csv").read_bytes()
    assert upload(api, "sites", "rango.csv", "sites.csv", dry_run=False).status_code == 422
    assert _events()[0].accion is A.CARGA_RECHAZADA and (data_dir / "sites.csv").read_bytes() == antes


def test_la_ip_del_cliente_se_guarda_solo_como_hash(api):
    api.post("/api/datos/validar/sites", headers={**KEY, "X-Forwarded-For": "198.51.100.9", "User-Agent": "prueba/2"},
             files={"file": ("sites.csv", fixture_bytes("sites_limites.csv"))})
    ev = _events()[0]
    assert ev.ip_hash == au.hash_ip("198.51.100.9") and ev.user_agent == "prueba/2"
    with db_session.session_scope() as s:
        assert "198.51.100.9" not in repr(s.execute(text("SELECT * FROM eventos_auditoria")).fetchall())


def test_la_cadena_de_las_cargas_es_valida(api):
    for nombre in ("sites_limites.csv", "rango.csv", "sites_valido.csv"):
        upload(api, "sites", nombre, "sites.csv", dry_run=False)
    r = au.verificar_cadena()
    assert r.valida and r.n_eventos == 3


def test_sin_base_de_datos_la_carga_sigue_funcionando(api, data_dir):
    db_session.configure(None)
    assert upload(api, "sites", "sites_limites.csv", "sites.csv", dry_run=False).status_code == 200
    assert (data_dir / "sites.csv").read_bytes() == fixture_bytes("sites_limites.csv")


# ------------------------------------------------------------------ fallar cerrado
def test_si_no_se_puede_firmar_el_manifiesto_no_se_guarda_nada(api, data_dir, monkeypatch):
    antes = (data_dir / "sites.csv").read_bytes()

    def sin_firma(*a, **k):
        raise pqc.ErrorClavesPQC("sin clave privada")
    monkeypatch.setattr(pqc, "generar_manifiesto", sin_firma)
    r = upload(api, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    assert r.status_code == 503 and r.json()["detail"] == c.MSG_SIGNING_UNAVAILABLE
    assert (data_dir / "sites.csv").read_bytes() == antes
    assert not (data_dir / pqc.MANIFEST_FILENAME).exists() and not (data_dir / "_historial").exists()
    assert _events() == [] and _cargas() == []                       # la auditoría se deshizo con el guardado
    assert not list(data_dir.glob(".tmp_*"))


def test_produccion_sin_claves_responde_503_y_no_guarda(api, data_dir, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "apogeo-backend")
    monkeypatch.delenv("PQC_PRIVATE_KEY")
    monkeypatch.delenv("PQC_PUBLIC_KEY")
    pqc.reiniciar()
    au.set_firmante()
    antes = (data_dir / "sites.csv").read_bytes()
    r = upload(api, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    assert r.status_code == 503
    assert (data_dir / "sites.csv").read_bytes() == antes and _events() == []
    assert upload(api, "sites", "sites_limites.csv", "sites.csv").status_code == 503      # el dry-run también falla cerrado


def test_arranque_en_produccion_sin_sal_falla(bd, data_dir, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "apogeo-backend")
    monkeypatch.delenv("IP_HASH_SALT", raising=False)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_arranque_en_produccion_con_sal_funciona(bd, data_dir, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "apogeo-backend")
    monkeypatch.setenv("IP_HASH_SALT", "sal")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200


# ------------------------------------------------------------------ manifiesto
def test_manifiesto_no_existe_hasta_la_primera_carga(api):
    r = api.get("/api/seguridad/manifiesto")
    assert r.status_code == 404 and r.json()["detail"] == c.MSG_NO_MANIFEST


def test_el_manifiesto_se_regenera_en_cada_carga_valida(api, data_dir):
    upload(api, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    r = api.get("/api/seguridad/manifiesto")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    m1 = r.json()
    nuevo = fixture_bytes("sites_limites.csv")
    assert {a["nombre"]: a["sha3_256"] for a in m1["archivos"]}["sites.csv"] == hashlib.sha3_256(nuevo).hexdigest()
    assert pqc.verificar_manifiesto(m1, data_dir) == []
    assert m1["huella_clave_publica"] == pqc.huella_clave_publica() and m1["algoritmo"] == "ML-DSA-65"
    upload(api, "sites", "sites_valido.csv", "sites.csv", dry_run=False)       # vuelve a tener sitio_uno
    assert upload(api, "efemerides", "efemerides_valido.csv", "efemerides_sitio_uno.csv", dry_run=False).status_code == 200
    m2 = api.get("/api/seguridad/manifiesto").json()
    assert {a["nombre"] for a in m2["archivos"]} == {"sites.csv", "efemerides_sitio_uno.csv"}
    assert pqc.verificar_manifiesto(m2, data_dir) == []


def test_el_manifiesto_se_devuelve_tal_cual_esta_en_disco(api, data_dir):
    upload(api, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    assert api.get("/api/seguridad/manifiesto").content == (data_dir / pqc.MANIFEST_FILENAME).read_bytes()


def test_validar_y_cargas_rechazadas_no_tocan_el_manifiesto(api, data_dir):
    upload(api, "sites", "sites_limites.csv", "sites.csv")                      # dry-run
    upload(api, "sites", "rango.csv", "sites.csv", dry_run=False)               # rechazada
    assert not (data_dir / pqc.MANIFEST_FILENAME).exists()


def test_manifiesto_alterado_se_detecta(api, data_dir):
    upload(api, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    (data_dir / "sites.csv").write_bytes(b"manipulado")
    assert pqc.verificar_manifiesto(json.loads((data_dir / pqc.MANIFEST_FILENAME).read_text()), data_dir) != []


def test_clave_publica_es_publica_y_coherente(api):
    r = api.get("/api/seguridad/clave-publica")
    assert r.status_code == 200
    body = r.json()
    assert body["algoritmo"] == pqc.ALGORITMO and body["estandar"] == pqc.ESTANDAR
    assert base64.b64decode(body["clave_publica_b64"]) == pqc.clave_publica()
    assert body["huella_sha3_256"] == pqc.huella_clave_publica() == hashlib.sha3_256(pqc.clave_publica()).hexdigest()


def test_clave_publica_en_produccion_sin_claves_da_503(api, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "x")
    monkeypatch.delenv("PQC_PRIVATE_KEY")
    monkeypatch.delenv("PQC_PUBLIC_KEY")
    pqc.reiniciar()
    assert api.get("/api/seguridad/clave-publica").status_code == 503


# ------------------------------------------------------------------ /api/auditoria
def test_auditoria_exige_api_key(api):
    assert api.get("/api/auditoria").status_code == 401
    assert api.get("/api/auditoria/verificar").status_code == 401
    assert api.get("/api/auditoria", headers={"X-API-Key": "mala"}).status_code == 401


def test_auditoria_503_si_no_hay_base_de_datos(api):
    db_session.configure(None)
    assert api.get("/api/auditoria", headers=KEY).status_code == 503


def test_listado_filtros_y_paginacion(api):
    upload(api, "sites", "sites_limites.csv", "sites.csv")
    upload(api, "sites", "rango.csv", "sites.csv")
    upload(api, "sites", "sites_valido.csv", "sites.csv", dry_run=False)
    r = api.get("/api/auditoria", headers=KEY)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and [e["secuencia"] for e in body["items"]] == [3, 2, 1]
    assert body["items"][0]["accion"] == "CARGA_GUARDADA" and "firma_b64" in body["items"][0]
    assert body["items"][0]["firma_b64"] is None
    assert api.get("/api/auditoria?accion=CARGA_RECHAZADA", headers=KEY).json()["total"] == 1
    assert api.get("/api/auditoria?usuario=api-key", headers=KEY).json()["total"] == 3
    assert api.get("/api/auditoria?usuario=otro", headers=KEY).json()["total"] == 0
    pagina = api.get("/api/auditoria?limite=1&offset=1", headers=KEY).json()
    assert pagina["total"] == 3 and [e["secuencia"] for e in pagina["items"]] == [2]
    assert api.get("/api/auditoria?incluir_firma=true", headers=KEY).json()["items"][0]["firma_b64"]


def test_listado_valida_parametros(api):
    assert api.get("/api/auditoria?limite=0", headers=KEY).status_code == 422
    assert api.get("/api/auditoria?limite=9999", headers=KEY).status_code == 422
    assert api.get("/api/auditoria?offset=-1", headers=KEY).status_code == 422
    assert api.get("/api/auditoria?accion=INVENTADA", headers=KEY).status_code == 422
    assert api.get("/api/auditoria?desde=2026-12-02T00:00:00Z&hasta=2026-12-01T00:00:00Z", headers=KEY).status_code == 422


def test_verificar_endpoint_valida_y_detecta_manipulacion(api):
    upload(api, "sites", "sites_limites.csv", "sites.csv")
    upload(api, "sites", "sites_valido.csv", "sites.csv")
    ok = api.get("/api/auditoria/verificar", headers=KEY).json()
    assert ok["valida"] is True and ok["n_eventos"] == 2 and ok["primer_hash_prev_es_genesis"] is True
    with db_session.session_scope() as s:
        s.execute(text("UPDATE eventos_auditoria SET actor = 'intruso' WHERE secuencia = 1"))
    mal = api.get("/api/auditoria/verificar", headers=KEY).json()
    assert mal["valida"] is False and mal["error_secuencia"] == 1 and mal["motivo"] == c.MSG_AUDIT_BAD_HASH

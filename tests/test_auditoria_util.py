"""auditoriaUtil: cadena de hashes, firma, detección de manipulación, privacidad y consulta."""
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core import config as c
from app.db import session as db_session
from app.db.enums import AccionAuditoria as A
from app.db.models import CargaArchivo, EventoAuditoria
from app.security.pqc import ErrorClavesPQC
from app.utils import auditoria_util as au

STUB_KEY = b"clave-de-prueba"


class StubFirmante:
    """Firmante de prueba (HMAC): mismo contrato que app.security.pqc, sin criptografía post-cuántica."""
    ALGORITMO = "STUB-HMAC"

    def __init__(self, huella="ab" * 32, key=STUB_KEY):
        self.huella, self.key = huella, key

    def huella_clave_publica(self, clave=None):
        return self.huella

    def firmar(self, datos, *, contexto=b""):
        return hmac.new(self.key, contexto + b"|" + datos, hashlib.sha256).digest()

    def verificar(self, datos, firma, *, contexto=b""):
        return hmac.compare_digest(self.firmar(datos, contexto=contexto), firma)


class FakeRequest:
    def __init__(self, ip="203.0.113.7", user_agent="pytest/1.0", forwarded=None):
        self.headers = {"user-agent": user_agent}
        if forwarded:
            self.headers["x-forwarded-for"] = forwarded
        self.client = type("C", (), {"host": ip})()


@pytest.fixture(autouse=True)
def entorno(bd, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("AUDIT_REQUIRE_SIGNATURE", raising=False)
    monkeypatch.setenv("IP_HASH_SALT", "sal-de-prueba")
    au.set_firmante(StubFirmante())


def _events(n=3):
    return [au.registrar_evento(A.LOGIN, "ana", recurso=f"r{i}", detalle={"i": i}) for i in range(n)]


def _all():
    with db_session.session_scope() as s:
        return s.query(EventoAuditoria).order_by(EventoAuditoria.secuencia).all()


def _sql(sql, **params):
    with db_session.session_scope() as s:
        s.execute(text(sql), params)


# ------------------------------------------------------------------ cadena y firma
def test_primer_evento_enlaza_con_el_genesis_y_los_siguientes_entre_si():
    e1, e2, e3 = _events()
    assert (e1.secuencia, e2.secuencia, e3.secuencia) == (1, 2, 3)
    assert e1.hash_prev == c.GENESIS_HASH and e2.hash_prev == e1.hash_actual and e3.hash_prev == e2.hash_actual


def test_hash_actual_es_sha3_de_hash_prev_mas_el_canonico():
    ev = _all()[0] if _events(1) else None
    canonical = au.canonical_event(ev.secuencia, ev.ts_utc, ev.usuario_id, ev.actor, ev.accion, ev.recurso, ev.detalle,
                                   ev.ip_hash, ev.user_agent, ev.algoritmo_firma, ev.huella_clave)
    assert ev.hash_actual == hashlib.sha3_256(ev.hash_prev.encode() + canonical).hexdigest()
    assert len(ev.hash_actual) == c.HASH_HEX_LENGTH


def test_cada_evento_guarda_firma_algoritmo_y_huella():
    ev = _events(1)[0]
    assert ev.algoritmo_firma == "STUB-HMAC" and ev.huella_clave == "ab" * 32 and ev.firma_pqc
    doc = au.event_signed_document(ev.secuencia, ev.hash_prev, ev.hash_actual, ev.ts_utc, ev.usuario_id, ev.accion,
                                   ev.algoritmo_firma, ev.huella_clave)
    assert StubFirmante().verificar(au.canonical_json(doc), ev.firma_pqc, contexto=c.AUDIT_SIGN_CONTEXT)
    assert not StubFirmante().verificar(au.canonical_json(doc), ev.firma_pqc, contexto=b"otro-dominio")


def test_el_canonico_incluye_secuencia_algoritmo_y_huella():
    ts = datetime(2026, 11, 14, tzinfo=timezone.utc)
    base = au.canonical_event(1, ts, None, "a", "LOGIN", None, {}, None, None, "ML-DSA-65", "h" * 64)
    assert base != au.canonical_event(2, ts, None, "a", "LOGIN", None, {}, None, None, "ML-DSA-65", "h" * 64)
    assert base != au.canonical_event(1, ts, None, "a", "LOGIN", None, {}, None, None, c.UNSIGNED_ALGORITHM, None)
    assert base != au.canonical_event(1, ts, None, "a", "LOGIN", None, {}, None, None, "ML-DSA-65", "i" * 64)


def test_canonico_es_estable_ante_el_orden_de_claves_y_las_zonas():
    ts_utc = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    ts_peru = ts_utc.astimezone(timezone(timedelta(hours=-5)))
    a = au.canonical_event(1, ts_utc, None, "a", "LOGIN", None, {"x": 1, "y": 2}, None, None, "A", "h")
    b = au.canonical_event(1, ts_peru, None, "a", "LOGIN", None, {"y": 2, "x": 1}, None, None, "A", "h")
    assert a == b
    assert au.iso_utc(datetime(2026, 11, 14, 12, 0)) == au.iso_utc(ts_utc)       # naive = UTC (SQLite)


def test_cadena_integra_es_valida():
    _events(5)
    r = au.verificar_cadena()
    assert r.valida and r.n_eventos == 5 and r.ultima_secuencia == 5 and r.primer_hash_prev_es_genesis is True
    assert r.hash_cabeza == _all()[-1].hash_actual


def test_cadena_vacia_es_valida():
    r = au.verificar_cadena()
    assert r.valida and r.n_eventos == 0 and r.hash_cabeza is None


# ------------------------------------------------------------------ manipulación
@pytest.mark.parametrize("sql,motivo", [
    ("UPDATE eventos_auditoria SET detalle = '{\"i\": 99}' WHERE secuencia = 2", c.MSG_AUDIT_BAD_HASH),
    ("UPDATE eventos_auditoria SET actor = 'intruso' WHERE secuencia = 2", c.MSG_AUDIT_BAD_HASH),
    ("UPDATE eventos_auditoria SET accion = 'LOGOUT' WHERE secuencia = 2", c.MSG_AUDIT_BAD_HASH),
    ("UPDATE eventos_auditoria SET usuario_id = '00000000000000000000000000000001' WHERE secuencia = 2", c.MSG_AUDIT_BAD_HASH),
    ("UPDATE eventos_auditoria SET ts_utc = '2020-01-01 00:00:00.000000' WHERE secuencia = 2", c.MSG_AUDIT_BAD_HASH),
    ("UPDATE eventos_auditoria SET ip_hash = 'x' WHERE secuencia = 2", c.MSG_AUDIT_BAD_HASH),
    ("UPDATE eventos_auditoria SET huella_clave = '" + "c" * 64 + "' WHERE secuencia = 2", c.MSG_AUDIT_BAD_HASH),
    ("UPDATE eventos_auditoria SET hash_prev = '" + "d" * 64 + "' WHERE secuencia = 2", c.MSG_AUDIT_BROKEN_LINK),
    ("UPDATE eventos_auditoria SET firma_pqc = x'00' WHERE secuencia = 2", c.MSG_AUDIT_BAD_SIGNATURE),
    ("DELETE FROM eventos_auditoria WHERE secuencia = 2", c.MSG_AUDIT_BAD_SEQUENCE),
    ("UPDATE eventos_auditoria SET secuencia = 9 WHERE secuencia = 3", c.MSG_AUDIT_BAD_SEQUENCE),
])
def test_manipulacion_se_detecta(sql, motivo):
    _events(3)
    _sql(sql)
    r = au.verificar_cadena()
    assert r.valida is False and r.motivo == motivo
    assert r.error_secuencia in (2, 3, 9)


def test_quitar_la_firma_se_detecta_aunque_se_declare_NINGUNO():
    _events(3)
    _sql("UPDATE eventos_auditoria SET firma_pqc = NULL, algoritmo_firma = 'NINGUNO' WHERE secuencia = 2")
    r = au.verificar_cadena()
    assert r.valida is False and r.motivo == c.MSG_AUDIT_BAD_HASH      # el algoritmo entra en el hash


def test_truncar_la_cola_no_se_detecta_sin_ancla_externa():
    """Limitación documentada: sin ancla externa un truncado de cola no se detecta."""
    _events(3)
    ancla = au.verificar_cadena().hash_cabeza
    _sql("DELETE FROM eventos_auditoria WHERE secuencia = 3")
    r = au.verificar_cadena()
    assert r.valida is True and r.hash_cabeza != ancla and r.ultima_secuencia == 2


def test_evento_sin_firma_en_desarrollo_se_guarda_pero_no_es_valido(caplog):
    au.set_firmante(None)
    with caplog.at_level(logging.WARNING, logger="apogeo.auditoria"):
        ev = au.registrar_evento(A.LOGIN, "ana")
    assert ev.algoritmo_firma == "NINGUNO" and ev.firma_pqc is None and ev.huella_clave is None
    r = au.verificar_cadena()
    assert r.valida is False and r.motivo == c.MSG_AUDIT_UNSIGNED
    assert "no disponible" in caplog.text.lower() or "firma" in caplog.text.lower()


def test_rotacion_de_clave_se_detecta_por_la_huella():
    _events(2)
    au.set_firmante(StubFirmante(huella="ef" * 32))
    r = au.verificar_cadena()
    assert r.valida is False and r.motivo == c.MSG_AUDIT_KEY_MISMATCH


def test_algoritmo_distinto_se_detecta():
    _events(2)

    class Otro(StubFirmante):
        ALGORITMO = "OTRO"
    au.set_firmante(Otro())
    assert au.verificar_cadena().motivo == c.MSG_AUDIT_ALGORITHM_MISMATCH


def test_sin_firmante_no_se_pueden_verificar_firmas():
    _events(2)
    au.set_firmante(None)
    assert au.verificar_cadena().motivo == c.MSG_AUDIT_CANNOT_VERIFY


# ------------------------------------------------------------------ producción: falla cerrado
def test_produccion_sin_firmante_no_registra_y_lanza(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "apogeo-backend")
    au.set_firmante(None)
    with pytest.raises(au.ErrorAuditoria):
        au.registrar_evento(A.LOGIN, "ana")
    assert _all() == []


def test_produccion_con_firma_que_falla_no_registra(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")

    class Roto(StubFirmante):
        def firmar(self, datos, *, contexto=b""):
            raise ErrorClavesPQC("sin clave privada")
    au.set_firmante(Roto())
    with pytest.raises(au.ErrorAuditoria):
        au.registrar_evento(A.LOGIN, "ana")
    assert _all() == []


def test_un_error_de_programacion_en_la_firma_no_se_degrada_en_silencio():
    class Bug(StubFirmante):
        def firmar(self, datos, *, contexto=b""):
            raise TypeError("bug")
    au.set_firmante(Bug())
    with pytest.raises(TypeError):
        au.registrar_evento(A.LOGIN, "ana")


def test_firma_obligatoria_por_variable_en_desarrollo(monkeypatch):
    monkeypatch.setenv("AUDIT_REQUIRE_SIGNATURE", "1")
    au.set_firmante(None)
    with pytest.raises(au.ErrorAuditoria):
        au.registrar_evento(A.LOGIN, "ana")


def test_produccion_con_base_de_datos_caida_lanza(bd, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "x")
    with db_session.session_scope() as s:
        s.execute(text("DROP TABLE eventos_auditoria"))
    with pytest.raises(au.ErrorAuditoria):
        au.registrar_evento(A.LOGIN, "ana")


def test_desarrollo_con_base_de_datos_caida_no_rompe_la_operacion(caplog):
    with db_session.session_scope() as s:
        s.execute(text("DROP TABLE eventos_auditoria"))
    with caplog.at_level(logging.ERROR, logger="apogeo.auditoria"):
        assert au.registrar_evento(A.LOGIN, "ana") is None
    assert "No se pudo registrar" in caplog.text and "sqlite" not in caplog.text.lower()   # sin URL de la base


def test_sin_base_de_datos_configurada_no_hace_nada():
    db_session.configure(None)
    assert au.registrar_evento(A.LOGIN, "ana") is None and au.registrar_carga(
        "sites", "sites.csv", b"x", 1, True, 0, A.CARGA_GUARDADA) is None


def test_detalle_demasiado_grande_se_rechaza():
    with pytest.raises(au.ErrorAuditoria):
        au.registrar_evento(A.CAMBIO_DATOS, "ana", detalle={"x": "y" * c.AUDIT_DETAIL_MAX_BYTES}, estricto=True)


# ------------------------------------------------------------------ privacidad
def test_la_ip_nunca_se_guarda_en_claro():
    ev = au.registrar_evento(A.LOGIN, "ana", request=FakeRequest(ip="203.0.113.7"))
    esperado = hmac.new(b"sal-de-prueba", b"203.0.113.7", hashlib.sha256).hexdigest()
    assert ev.ip_hash == esperado
    with db_session.session_scope() as s:
        fila = s.execute(text("SELECT * FROM eventos_auditoria")).fetchall()
        assert "203.0.113.7" not in repr(fila)


def test_x_forwarded_for_usa_el_primer_valor():
    ev = au.registrar_evento(A.LOGIN, "ana", request=FakeRequest(ip="10.0.0.1", forwarded="198.51.100.9, 10.0.0.1"))
    assert ev.ip_hash == au.hash_ip("198.51.100.9")


def test_sin_sal_no_se_guarda_hash_de_ip(monkeypatch):
    monkeypatch.delenv("IP_HASH_SALT")
    assert au.registrar_evento(A.LOGIN, "ana", request=FakeRequest()).ip_hash is None


def test_la_sal_cambia_el_hash(monkeypatch):
    a = au.hash_ip("1.2.3.4")
    monkeypatch.setenv("IP_HASH_SALT", "otra")
    assert au.hash_ip("1.2.3.4") != a and au.hash_ip(None) is None


def test_user_agent_se_recorta():
    ev = au.registrar_evento(A.LOGIN, "ana", request=FakeRequest(user_agent="u" * 500))
    assert len(ev.user_agent) == c.DB_USER_AGENT_MAX


# ------------------------------------------------------------------ cargas y login
def test_registrar_carga_guarda_fila_firmada_y_evento():
    contenido = b"site_id,nombre\n1,2\n"
    reg = au.registrar_carga("sites", "sites.csv", contenido, 1, True, 0, A.CARGA_GUARDADA, request=FakeRequest(),
                             ruta_almacenamiento="/data/sites.csv", ruta_historial="sites_x.csv")
    digest = hashlib.sha3_256(contenido).hexdigest()
    assert reg.evento.accion is A.CARGA_GUARDADA and reg.evento.detalle["sha3_256"] == digest
    with db_session.session_scope() as s:
        carga = s.query(CargaArchivo).one()
        assert carga.sha3_256 == digest and carga.tamano_bytes == len(contenido) and carga.actor == "api-key"
        assert carga.ruta_historial == "sites_x.csv" and carga.algoritmo_firma == "STUB-HMAC"
        doc = {"v": 1, "sha3_256": digest, "nombre_archivo": "sites.csv", "tipo": "sites", "tamano_bytes": len(contenido),
               "algoritmo_firma": "STUB-HMAC", "huella_clave": "ab" * 32}
        assert StubFirmante().verificar(au.canonical_json(doc), carga.firma_pqc, contexto=c.UPLOAD_SIGN_CONTEXT)
    assert au.verificar_cadena().valida


def test_registrar_carga_con_sesion_ajena_no_hace_commit():
    with db_session.session_scope() as s:
        au.registrar_carga("sites", "sites.csv", b"x", 1, True, 0, A.CARGA_GUARDADA, db=s)
        s.rollback()
    assert _all() == []


def test_login_y_login_fallido():
    uid = uuid.uuid4()
    ok = au.registrar_login(uid, "ana@example.org", request=FakeRequest())
    ko = au.registrar_login_fallido("desconocido", request=FakeRequest(), motivo="clave incorrecta")
    assert ok.accion is A.LOGIN and ok.usuario_id == uid and ko.accion is A.LOGIN_FALLIDO
    assert ko.detalle == {"motivo": "clave incorrecta"} and au.verificar_cadena().valida


# ------------------------------------------------------------------ consulta
def test_consultar_filtra_pagina_y_ordena():
    uid = uuid.uuid4()
    au.registrar_evento(A.LOGIN, "ana", usuario_id=uid)
    au.registrar_evento(A.CARGA_GUARDADA, "api-key")
    au.registrar_evento(A.CARGA_RECHAZADA, "api-key")
    au.registrar_evento(A.LOGOUT, "ana", usuario_id=uid)
    items, total = au.consultar()
    assert total == 4 and [e.secuencia for e in items] == [4, 3, 2, 1]
    assert au.consultar(usuario="api-key")[1] == 2
    assert au.consultar(usuario=uid)[1] == 2
    assert au.consultar(accion=A.LOGOUT)[0][0].secuencia == 4
    items, total = au.consultar(limite=2, offset=1)
    assert total == 4 and [e.secuencia for e in items] == [3, 2]
    ahora = datetime.now(timezone.utc)
    assert au.consultar(desde=ahora + timedelta(minutes=1))[1] == 0
    assert au.consultar(hasta=ahora + timedelta(minutes=1))[1] == 4
    assert au.consultar(limite=10_000)[0] and au.consultar(limite=0)[0]       # el límite se acota a [1, máx]


# ------------------------------------------------------------------ con la firma real ML-DSA-65
@pytest.fixture
def pqc_real(monkeypatch):
    from app.security import pqc
    privada, publica = pqc.generar_par()
    monkeypatch.setenv("PQC_PRIVATE_KEY", privada)
    monkeypatch.setenv("PQC_PUBLIC_KEY", publica)
    pqc.reiniciar()
    au.set_firmante()
    yield pqc
    pqc.reiniciar()


def test_cadena_con_ml_dsa_65_real(pqc_real):
    _events(3)
    ev = _all()[0]
    assert ev.algoritmo_firma == "ML-DSA-65" and len(ev.firma_pqc) == 3309 and ev.huella_clave == pqc_real.huella_clave_publica()
    assert au.verificar_cadena().valida
    _sql("UPDATE eventos_auditoria SET detalle = '{\"i\": 7}' WHERE secuencia = 1")
    assert au.verificar_cadena().motivo == c.MSG_AUDIT_BAD_HASH


def test_firma_real_cubre_el_canonico_de_pqc(pqc_real):
    ev = _events(1)[0]
    doc = au.event_signed_document(ev.secuencia, ev.hash_prev, ev.hash_actual, ev.ts_utc, ev.usuario_id, ev.accion,
                                   ev.algoritmo_firma, ev.huella_clave)
    assert au.canonical_json(doc) == pqc_real.canonicalizar(doc)
    assert pqc_real.verificar(pqc_real.canonicalizar(doc), ev.firma_pqc, contexto=b"auditoria")
    assert not pqc_real.verificar(pqc_real.canonicalizar(doc), ev.firma_pqc, contexto=b"carga-csv")


def test_rotar_la_clave_real_invalida_la_verificacion(pqc_real, monkeypatch):
    _events(2)
    privada, publica = pqc_real.generar_par()
    monkeypatch.setenv("PQC_PRIVATE_KEY", privada)
    monkeypatch.setenv("PQC_PUBLIC_KEY", publica)
    pqc_real.reiniciar()
    assert au.verificar_cadena().motivo == c.MSG_AUDIT_KEY_MISMATCH


# ------------------------------------------------------------------ saneo, tipos, ancla y producción sin base
def test_actor_recurso_y_nombre_se_sanean_y_recortan():
    ev = au.registrar_evento(A.CAMBIO_DATOS, "a\x00" + "x" * 300, recurso="r\x07" + "y" * 500)
    assert "\x00" not in ev.actor and len(ev.actor) == c.DB_ACTOR_MAX
    assert "\x07" not in ev.recurso and len(ev.recurso) == c.DB_RESOURCE_MAX
    reg = au.registrar_carga("sites", "n\x01" + "z" * 400 + ".csv", b"x", 1, True, 0, A.CARGA_GUARDADA, actor="q" * 200)
    assert len(reg.carga.nombre_archivo) == c.DB_FILENAME_MAX and len(reg.carga.actor) == c.DB_ACTOR_MAX


def test_motivo_de_login_fallido_se_recorta():
    ev = au.registrar_login_fallido("x", motivo="m" * 1000)
    assert len(ev.detalle["motivo"]) == c.DB_RESOURCE_MAX


@pytest.mark.parametrize("detalle", [{"x": 1.5}, {"x": object()}, {"x": {1: "a"}}, {"x": [datetime.now()]}, {"x": b"bytes"}])
def test_detalle_con_tipos_no_permitidos_se_rechaza(detalle):
    with pytest.raises(au.ErrorAuditoria):
        au.registrar_evento(A.CAMBIO_DATOS, "ana", detalle=detalle, estricto=True)


def test_detalle_con_tipos_validos_anidados():
    ev = au.registrar_evento(A.CAMBIO_DATOS, "ana", detalle={"a": [1, "b", True, None, {"c": "d"}]})
    assert au.verificar_cadena().valida and ev.detalle["a"][4] == {"c": "d"}


def test_el_ancla_se_emite_al_confirmar_y_no_si_hay_rollback(caplog):
    with caplog.at_level(logging.INFO, logger="apogeo.auditoria"):
        with pytest.raises(RuntimeError):
            with db_session.session_scope() as s:
                au.registrar_evento(A.LOGIN, "ana", db=s)
                raise RuntimeError("deshacer")
        assert "auditoria cabeza" not in caplog.text and _all() == []
        ev = au.registrar_evento(A.LOGIN, "ana")
    assert f"secuencia=1 hash={ev.hash_actual}" in caplog.text           # tras el rollback, la secuencia 1 se reutiliza


def test_el_log_no_contiene_la_ip(caplog):
    with caplog.at_level(logging.DEBUG, logger="apogeo.auditoria"):
        au.registrar_evento(A.LOGIN, "ana", request=FakeRequest(ip="203.0.113.77"))
    assert "203.0.113.77" not in caplog.text


def test_produccion_sin_base_de_datos_configurada_lanza(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "x")
    db_session.configure(None)
    with pytest.raises(au.ErrorAuditoria):
        au.registrar_evento(A.LOGIN, "ana")
    with pytest.raises(au.ErrorAuditoria):
        with au.unidad_de_trabajo():
            pass

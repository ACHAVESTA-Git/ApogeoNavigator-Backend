"""Login local: credenciales, sesión en cookie, límite de intentos, logout, roles, CSRF, auditoría y secretos fuera de los logs."""
import logging
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core import config as c
from app.db import session as db_session
from app.db.base import Base
from app.db.enums import AccionAuditoria, RolUsuario
from app.db.models import EventoAuditoria, Sesion, Usuario, utcnow
from app.main import app
from app.models.claves import ClaveUsuario, EstadoClave, TipoClave
from app.security import gestor_claves as gk
from app.security import passwords, pqc
from app.services.auth import hash_token, reiniciar_limites
from app.services.usuarios import crear_usuario
from app.utils import auditoria_util

ADMIN_KEY = "admin-secreto"
UPLOAD_KEY = "clave-de-carga"
PASSWORD = "Contraseña-Larga-Y-Unica-1"
CSRF = {c.AUTH_CSRF_HEADER: "fetch"}
SITES = (Path(__file__).parent.parent / "data" / "sites.csv").read_bytes()


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    for nombre in (pqc.ENV_CLOUD_RUN_MARKER, pqc.ENV_ENVIRONMENT, c.ENV_KEM_PRIVATE, c.ENV_EMAIL_HASH_KEY):
        monkeypatch.delenv(nombre, raising=False)
    privada, publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, publica)
    monkeypatch.setenv(pqc.ENV_DEV_KEY_DIR, str(tmp_path / "dev"))
    monkeypatch.setenv(c.ENV_ADMIN_API_KEY, ADMIN_KEY)
    monkeypatch.setenv(c.ENV_UPLOAD_API_KEY, UPLOAD_KEY)
    monkeypatch.setenv(c.ENV_DATA_DIR, str(tmp_path / "data"))
    monkeypatch.setenv(c.ENV_IP_HASH_SALT, "sal-de-prueba")
    (tmp_path / "data").mkdir()
    pqc.reiniciar()
    gk.reiniciar()
    reiniciar_limites()
    auditoria_util.set_firmante()
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(engine)
    db_session.configure(engine)
    yield engine
    db_session.configure(None)
    pqc.reiniciar()
    gk.reiniciar()
    reiniciar_limites()


@pytest.fixture
def engine(entorno):
    return entorno


@pytest.fixture
def client():
    return TestClient(app)


def _usuario(engine, email="ana@apogeo.test", rol=RolUsuario.ANALISTA, activo=True, password=PASSWORD, nombre="Ana Ruiz") -> uuid.UUID:
    with Session(engine) as s:
        u = crear_usuario(s, email, nombre, rol=rol, activo=activo, password_hash=passwords.hash_password(password) if password else None)
        s.commit()
        return u.id


def _entrar(client, email="ana@apogeo.test", password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _eventos(engine, accion):
    with Session(engine) as s:
        return list(s.scalars(select(EventoAuditoria).where(EventoAuditoria.accion == accion)))


# ------------------------------------------------------------------ login
def test_login_correcto_abre_sesion_con_cookie_segura_y_perfil_sin_privadas(client, engine):
    uid = _usuario(engine)
    with Session(engine) as s:
        for tipo in ("kem", "firma"):
            par = gk.generar_par(tipo)
            s.add(ClaveUsuario(id=uuid.uuid4(), usuario_id=uid, tipo=TipoClave(tipo), algoritmo=par.algoritmo, clave_publica=par.publica,
                               huella_sha3_256=par.huella, tamano_publica=par.tamano_publica, tamano_privada=par.tamano_privada,
                               estado=EstadoClave.ACTIVA, backups=0))
        s.commit()
    r = _entrar(client)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["id"] == str(uid) and cuerpo["rol"] == "analista" and cuerpo["nombre"] == "Ana Ruiz"
    assert sorted(k["algoritmo"] for k in cuerpo["claves"]) == ["ML-DSA-65", "ML-KEM-1024"]
    assert "privada" not in r.text.lower() and "BEGIN" not in r.text and set(cuerpo) == {"id", "nombre", "rol", "claves"}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=28800" in cookie and "secure" not in cookie
    assert r.headers["cache-control"] == "no-store"
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json() == cuerpo


def test_en_produccion_la_cookie_es_secure(client, engine, monkeypatch):
    _usuario(engine)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert "secure" in _entrar(client).headers["set-cookie"].lower()


def test_en_la_base_solo_esta_el_hash_del_token_y_la_contrasena_es_argon2id(client, engine):
    _usuario(engine)
    token = _entrar(client).cookies[c.AUTH_COOKIE_NAME]
    with Session(engine) as s:
        sesion = s.scalars(select(Sesion)).one()
        assert sesion.token_hash == hash_token(token) and token not in (sesion.token_hash, sesion.user_agent or "")
        assert s.scalars(select(Usuario)).one().password_hash.startswith("$argon2id$")


def test_credenciales_invalidas_dan_la_misma_respuesta_exista_o_no_el_email(client, engine):
    _usuario(engine)
    _usuario(engine, "baja@apogeo.test", activo=False)
    _usuario(engine, "sinlogin@apogeo.test", password=None)
    respuestas = [_entrar(client, "ana@apogeo.test", "mal"), _entrar(client, "no-existe@apogeo.test", PASSWORD),
                  _entrar(client, "baja@apogeo.test", PASSWORD), _entrar(client, "sinlogin@apogeo.test", PASSWORD)]
    assert {r.status_code for r in respuestas} == {401}
    assert {r.json()["detail"] for r in respuestas} == {c.MSG_LOGIN_INVALID}
    assert {r.json()["error"]["codigo"] for r in respuestas} == {"CREDENCIALES_INVALIDAS"}
    assert all("set-cookie" not in r.headers for r in respuestas)


def test_el_email_no_distingue_mayusculas(client, engine):
    _usuario(engine)
    assert _entrar(client, "  ANA@Apogeo.TEST ").status_code == 200


def test_entradas_mal_formadas_son_422(client):
    assert client.post("/api/auth/login", json={"email": "a@b.c"}).status_code == 422
    assert client.post("/api/auth/login", json={"email": "a@b.c", "password": "x" * (c.AUTH_PASSWORD_MAX + 1)}).status_code == 422


# ------------------------------------------------------------------ límite de intentos
def test_el_sexto_intento_fallido_por_email_da_429_con_retry_after(client, engine):
    _usuario(engine)
    for _ in range(c.AUTH_LOGIN_LIMIT_EMAIL[0]):
        assert _entrar(client, password="mal").status_code == 401
    r = _entrar(client)                                      # ni la contraseña correcta entra mientras dura el bloqueo
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["error"]["codigo"] == "DEMASIADOS_INTENTOS"


def test_el_limite_por_ip_cubre_muchos_emails(client, engine):
    for i in range(c.AUTH_LOGIN_LIMIT_IP[0]):
        assert _entrar(client, f"x{i}@apogeo.test", "mal").status_code == 401
    assert _entrar(client, "otro@apogeo.test", "mal").status_code == 429


def test_un_login_correcto_borra_los_fallos_del_email(client, engine):
    _usuario(engine)
    for _ in range(c.AUTH_LOGIN_LIMIT_EMAIL[0] - 1):
        _entrar(client, password="mal")
    assert _entrar(client).status_code == 200
    for _ in range(c.AUTH_LOGIN_LIMIT_EMAIL[0] - 1):
        assert _entrar(client, password="mal").status_code == 401


# ------------------------------------------------------------------ sesión
def test_me_sin_sesion_es_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["error"]["codigo"] == "SESION_INVALIDA"


def test_una_sesion_caducada_no_vale(client, engine):
    _usuario(engine)
    _entrar(client)
    with Session(engine) as s:
        sesion = s.scalars(select(Sesion)).one()
        sesion.expira_utc = utcnow() - timedelta(seconds=1)
        s.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_la_sesion_dura_8_horas(client, engine):
    _usuario(engine)
    _entrar(client)
    with Session(engine) as s:
        sesion = s.scalars(select(Sesion)).one()
        restante = sesion.expira_utc.replace(tzinfo=utcnow().tzinfo) - utcnow()
        assert timedelta(hours=7, minutes=59) < restante <= timedelta(hours=8)


def test_un_usuario_desactivado_pierde_la_sesion(client, engine):
    uid = _usuario(engine)
    _entrar(client)
    with Session(engine) as s:
        s.get(Usuario, uid).activo = False
        s.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_un_token_inventado_no_vale(client):
    client.cookies.set(c.AUTH_COOKIE_NAME, "token-inventado")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revoca_la_sesion_y_borra_la_cookie(client, engine):
    _usuario(engine)
    _entrar(client)
    token = client.cookies[c.AUTH_COOKIE_NAME]
    r = client.post("/api/auth/logout", headers=CSRF)
    assert r.status_code == 204 and "max-age=0" in r.headers["set-cookie"].lower()
    client.cookies.set(c.AUTH_COOKIE_NAME, token)               # aunque alguien conserve el token, ya no sirve
    assert client.get("/api/auth/me").status_code == 401
    with Session(engine) as s:
        assert s.scalars(select(Sesion)).one().revocada is True
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204       # idempotente


def test_logout_exige_la_cabecera_anti_csrf(client, engine):
    _usuario(engine)
    _entrar(client)
    r = client.post("/api/auth/logout")
    assert r.status_code == 403 and r.json()["error"]["codigo"] == "ORIGEN_NO_PERMITIDO"
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": "http://localhost:5173"}).status_code == 204


def test_login_rota_el_token_y_revoca_el_anterior(client, engine):
    _usuario(engine)
    _entrar(client)
    viejo = client.cookies[c.AUTH_COOKIE_NAME]
    _entrar(client)
    nuevo = client.cookies[c.AUTH_COOKIE_NAME]
    assert viejo != nuevo
    with Session(engine) as s:
        por_hash = {x.token_hash: x.revocada for x in s.scalars(select(Sesion))}
    assert por_hash[hash_token(viejo)] is True and por_hash[hash_token(nuevo)] is False


# ------------------------------------------------------------------ roles y CSRF en los endpoints protegidos
def _validar(client, **extra):
    return client.post("/api/datos/validar/sites", files={"file": ("sites.csv", SITES, "text/csv")}, **extra)


@pytest.mark.parametrize("rol", list(RolUsuario))
def test_carga_con_sesion_de_analista_revisor_o_admin(client, engine, rol):
    _usuario(engine, rol=rol)
    _entrar(client)
    assert _validar(client, headers=CSRF).status_code == 200


def test_carga_con_sesion_sin_cabecera_anti_csrf_es_403(client, engine):
    _usuario(engine)
    _entrar(client)
    r = _validar(client)
    assert r.status_code == 403 and r.json()["error"]["codigo"] == "ORIGEN_NO_PERMITIDO"


def test_carga_sigue_aceptando_la_api_key_y_sigue_dando_401_sin_credenciales(client):
    assert _validar(client, headers={c.API_KEY_HEADER: UPLOAD_KEY}).status_code == 200
    r = _validar(client)
    assert (r.status_code, r.json()["detail"]) == (401, c.MSG_API_KEY_INVALID)


def test_gestor_de_claves_exige_rol_admin_con_el_codigo_estandarizado(client, engine):
    _usuario(engine, rol=RolUsuario.ANALISTA)
    _entrar(client)
    r = client.get("/api/admin/claves", headers=CSRF)
    assert r.status_code == 403 and r.json()["error"]["codigo"] == "ROL_INSUFICIENTE" and r.json()["detail"] == c.MSG_ROLE_FORBIDDEN


def test_gestor_de_claves_con_sesion_admin_y_con_api_key(client, engine):
    _usuario(engine, "admin@apogeo.test", rol=RolUsuario.ADMIN, nombre="Admin")
    _entrar(client, "admin@apogeo.test")
    assert client.get("/api/admin/claves").status_code == 200
    anonimo = TestClient(app)
    assert anonimo.get("/api/admin/claves").status_code == 401
    assert anonimo.get("/api/admin/claves", headers={c.API_KEY_HEADER: ADMIN_KEY}).status_code == 200


def test_el_revisor_no_puede_usar_el_gestor_de_claves(client, engine):
    _usuario(engine, rol=RolUsuario.REVISOR)
    _entrar(client)
    assert client.get("/api/admin/claves").status_code == 403


# ------------------------------------------------------------------ auditoría y secretos
def test_login_y_login_fallido_quedan_en_la_auditoria_sin_secretos(client, engine):
    uid = _usuario(engine)
    _entrar(client, password="mal")
    _entrar(client)
    client.post("/api/auth/logout", headers=CSRF)
    fallidos, correctos, salidas = (_eventos(engine, a) for a in (AccionAuditoria.LOGIN_FALLIDO, AccionAuditoria.LOGIN, AccionAuditoria.LOGOUT))
    assert len(fallidos) == len(correctos) == len(salidas) == 1 and correctos[0].usuario_id == uid
    with Session(engine) as s:
        texto = repr([(e.detalle, e.recurso, e.actor) for e in s.scalars(select(EventoAuditoria))])
    assert PASSWORD not in texto and "ana@apogeo.test" not in texto
    assert fallidos[0].detalle == {"motivo": c.MOTIVO_LOGIN_CREDENCIALES}


def test_el_limite_de_intentos_tambien_se_audita(client, engine):
    _usuario(engine)
    for _ in range(c.AUTH_LOGIN_LIMIT_EMAIL[0] + 1):
        _entrar(client, password="mal")
    motivos = [e.detalle["motivo"] for e in _eventos(engine, AccionAuditoria.LOGIN_FALLIDO)]
    assert motivos.count(c.MOTIVO_LOGIN_LIMITE) == 1


def test_la_contrasena_y_el_token_no_aparecen_en_los_logs(client, engine, caplog):
    _usuario(engine)
    with caplog.at_level(logging.DEBUG):
        _entrar(client, password="clave-erronea-secreta")
        token = _entrar(client).cookies[c.AUTH_COOKIE_NAME]
        client.get("/api/auth/me")
    assert "clave-erronea-secreta" not in caplog.text and PASSWORD not in caplog.text and token not in caplog.text
    assert "ana@apogeo.test" not in caplog.text


def test_la_cookie_pasa_el_guardia_de_subida_pero_sin_sesion_valida_es_401(client):
    client.cookies.set(c.AUTH_COOKIE_NAME, "falsa")
    r = _validar(client, headers=CSRF)
    assert r.status_code == 401

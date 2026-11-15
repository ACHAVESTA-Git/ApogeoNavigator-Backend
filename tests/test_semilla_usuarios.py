"""Semilla de usuarios de prueba: 3 roles con claves PQC, idempotente, sin secretos fuera del archivo 600 y bloqueada en producción."""
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.cli import seed_usuarios
from app.core import config as c
from app.db import session as db_session
from app.db.base import Base
from app.db.enums import AccionAuditoria
from app.db.models import EventoAuditoria, Usuario
from app.main import app
from app.models.claves import ClaveUsuario, EstadoClave
from app.security import gestor_claves as gk
from app.security import pqc
from app.services.auth import reiniciar_limites
from app.utils import auditoria_util

RAIZ = Path(__file__).parent.parent
ROLES = ("analista", "revisor", "admin")


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    for nombre in (pqc.ENV_CLOUD_RUN_MARKER, pqc.ENV_ENVIRONMENT, c.ENV_KEM_PRIVATE, c.ENV_EMAIL_HASH_KEY, c.ENV_SEED_CREDENTIALS_FILE):
        monkeypatch.delenv(nombre, raising=False)
    privada, publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, publica)
    monkeypatch.setenv(pqc.ENV_DEV_KEY_DIR, str(tmp_path / "dev"))
    pqc.reiniciar()
    gk.reiniciar()
    reiniciar_limites()
    auditoria_util.set_firmante()
    engine = create_engine(f"sqlite:///{tmp_path / 'semilla.db'}")
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
def salida(tmp_path):
    return tmp_path / "credenciales.local"


def _correr(salida, *extra):
    return seed_usuarios.main(["--salida", str(salida), *extra])


def _credenciales(salida) -> dict[str, str]:
    """{email: contraseña} leído del archivo de credenciales."""
    resultado, actual = {}, None
    for linea in salida.read_text().splitlines():
        if linea.startswith("[") and linea.endswith("]"):
            actual = linea[1:-1]
        elif linea.startswith("contrasena: ") and actual:
            resultado[actual] = linea.split(": ", 1)[1]
    return resultado


def test_crea_los_tres_usuarios_con_su_rol_y_dos_claves_activas_cada_uno(engine, salida, capsys):
    assert _correr(salida) == 0
    with Session(engine) as s:
        por_email = {u.email_hash: u for u in s.scalars(select(Usuario))}
        assert len(por_email) == 3
        assert sorted(u.rol.value for u in por_email.values()) == sorted(ROLES)
        for u in por_email.values():
            assert gk.descifrar_email(u.email_cifrado).endswith("@apogeo.test") and u.password_hash.startswith("$argon2id$")
            claves = list(s.scalars(select(ClaveUsuario).where(ClaveUsuario.usuario_id == u.id)))
            assert sorted(k.algoritmo for k in claves) == ["ML-DSA-65", "ML-KEM-1024"]
            assert {k.estado for k in claves} == {EstadoClave.ACTIVA} and {k.backups for k in claves} == {1}
    assert {e: None for e in _credenciales(salida)} == {f"{r}@apogeo.test": None for r in ROLES}


def test_el_archivo_tiene_permisos_600_y_lleva_las_privadas_y_las_contrasenas(salida):
    _correr(salida)
    assert stat.S_IMODE(salida.stat().st_mode) == 0o600
    texto = salida.read_text()
    assert texto.count("-----BEGIN PRIVATE KEY-----") == 6 and "NO SUBIR AL REPOSITORIO" in texto
    assert all(len(p) >= 20 for p in _credenciales(salida).values()) and len(set(_credenciales(salida).values())) == 3


def test_ni_la_salida_ni_los_logs_ni_la_base_llevan_secretos(engine, salida, capsys, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        _correr(salida)
    salida_cli = capsys.readouterr()
    secretos = list(_credenciales(salida).values()) + ["BEGIN PRIVATE KEY"]
    for texto in (salida_cli.out, salida_cli.err, caplog.text):
        assert not any(sec in texto for sec in secretos)
    assert "creado: analista@apogeo.test" in salida_cli.out and str(salida) in salida_cli.out
    with Session(engine) as s:
        volcado = repr([(e.detalle, e.recurso) for e in s.scalars(select(EventoAuditoria))])
        volcado += repr([u.password_hash for u in s.scalars(select(Usuario))])
    assert not any(sec in volcado for sec in list(_credenciales(salida).values()) + ["BEGIN PRIVATE KEY"])


def test_es_idempotente(engine, salida, capsys):
    _correr(salida)
    antes = salida.read_text()
    capsys.readouterr()
    assert _correr(salida) == 0
    assert "ya existía: admin@apogeo.test" in capsys.readouterr().out
    assert salida.read_text() == antes
    with Session(engine) as s:
        assert len(list(s.scalars(select(Usuario)))) == 3 and len(list(s.scalars(select(ClaveUsuario)))) == 6


def test_si_falta_uno_solo_crea_ese_y_no_toca_a_los_demas(engine, salida):
    _correr(salida)
    with Session(engine) as s:
        revisor = next(u for u in s.scalars(select(Usuario)) if u.rol.value == "revisor")
        hash_analista = next(u.password_hash for u in s.scalars(select(Usuario)) if u.rol.value == "analista")
        s.delete(revisor)
        s.query(ClaveUsuario).filter(ClaveUsuario.usuario_id == revisor.id).delete()
        s.commit()
    _correr(salida)
    with Session(engine) as s:
        assert sorted(u.rol.value for u in s.scalars(select(Usuario))) == sorted(ROLES)
        assert next(u.password_hash for u in s.scalars(select(Usuario)) if u.rol.value == "analista") == hash_analista
    assert salida.read_text().count("[revisor@apogeo.test]") == 2          # el bloque original y el nuevo (se añade, no se pisa)
    assert salida.read_text().count("[analista@apogeo.test]") == 1


def test_las_credenciales_del_archivo_inician_sesion(engine, salida):
    _correr(salida)
    cliente = TestClient(app)
    for email, password in _credenciales(salida).items():
        r = cliente.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200 and sorted(k["algoritmo"] for k in r.json()["claves"]) == ["ML-DSA-65", "ML-KEM-1024"]
        assert "privada" not in r.text.lower()


def test_la_creacion_de_claves_queda_en_la_auditoria(engine, salida):
    _correr(salida)
    with Session(engine) as s:
        eventos = list(s.scalars(select(EventoAuditoria).where(EventoAuditoria.accion == AccionAuditoria.CAMBIO_DATOS)))
    assert len(eventos) == 6


def test_en_produccion_no_corre_sin_el_flag(engine, salida, monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _correr(salida) == 2
    assert c.FLAG_FORCE_PRODUCTION in capsys.readouterr().err and not salida.exists()
    with Session(engine) as s:
        assert list(s.scalars(select(Usuario))) == []


def test_en_produccion_con_el_flag_si_corre(engine, salida, monkeypatch):
    kem, clave_hash = gk.generar_claves_servidor()           # en producción las claves del servidor son obligatorias
    monkeypatch.setenv(c.ENV_KEM_PRIVATE, kem)
    monkeypatch.setenv(c.ENV_EMAIL_HASH_KEY, clave_hash)
    gk.reiniciar()
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _correr(salida, c.FLAG_FORCE_PRODUCTION) == 0 and salida.exists()


def test_sin_base_de_datos_falla_con_un_mensaje_claro(salida, capsys):
    db_session.configure(None)
    assert _correr(salida) == 2 and c.MSG_SEED_NO_DB in capsys.readouterr().err


def test_la_ruta_por_defecto_sale_de_la_variable_de_entorno(engine, tmp_path, monkeypatch):
    destino = tmp_path / "desde_env.local"
    monkeypatch.setenv(c.ENV_SEED_CREDENTIALS_FILE, str(destino))
    assert seed_usuarios.main([]) == 0 and destino.exists()


def test_el_archivo_de_credenciales_esta_en_gitignore_y_dockerignore():
    for nombre in (".gitignore", ".dockerignore"):
        assert c.SEED_CREDENTIALS_DEFAULT in (RAIZ / nombre).read_text().splitlines()

"""Email de usuarios cifrado en reposo: servicio, rotación de claves y migraciones 0003 y 0004."""
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import config
from app.db.base import Base
from app.db.models import Usuario
from app.security import gestor_claves as gk
from app.services import usuarios as svc

ROOT = Path(__file__).resolve().parents[1]


def poner_claves(monkeypatch, anteriores=None):
    """Claves del servidor nuevas en el entorno; `anteriores` = (semilla KEM, clave de hash) que pasan a _ANTERIORES."""
    semilla, clave_hash = gk.generar_claves_servidor()
    monkeypatch.setenv(config.ENV_KEM_PRIVATE, semilla)
    monkeypatch.setenv(config.ENV_EMAIL_HASH_KEY, clave_hash)
    for var in (config.ENV_KEM_PREVIOUS, config.ENV_EMAIL_HASH_PREVIOUS):
        monkeypatch.delenv(var, raising=False)
    if anteriores:
        monkeypatch.setenv(config.ENV_KEM_PREVIOUS, anteriores[0])
        monkeypatch.setenv(config.ENV_EMAIL_HASH_PREVIOUS, anteriores[1])
    gk.reiniciar()
    return semilla, clave_hash


@pytest.fixture(autouse=True)
def claves(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    claves = poner_claves(monkeypatch)
    yield claves
    gk.reiniciar()


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


# ------------------------------------------------------------------ servicio
def test_el_email_se_guarda_cifrado_y_nunca_en_claro(engine):
    with Session(engine) as s:
        u = svc.crear_usuario(s, "Ana.Ruiz@Example.org", "Ana")
        s.commit()
        assert svc.email_de(u) == "ana.ruiz@example.org"                       # normalizado
        fila = s.execute(text("SELECT * FROM usuarios")).fetchall()
        assert "ana.ruiz" not in repr(fila).lower() and "example.org" not in repr(fila).lower()
        assert len(u.email_cifrado) > 1000 and len(u.email_hash) == 64


def test_buscar_por_email_exacto_sin_distinguir_mayusculas_ni_espacios(engine):
    with Session(engine) as s:
        u = svc.crear_usuario(s, "ana@example.org", "Ana")
        svc.crear_usuario(s, "luis@example.org", "Luis")
        s.commit()
        assert svc.buscar_por_email(s, "  ANA@example.org ").id == u.id
        assert svc.buscar_por_email(s, "ana@example.com") is None
        assert svc.buscar_por_email(s, "ana") is None                           # no hay búsqueda parcial


def test_email_repetido_falla_por_hash_unico(engine):
    with Session(engine) as s:
        svc.crear_usuario(s, "ana@example.org", "Ana")
        svc.crear_usuario(s, "ANA@example.org", "Otra Ana")
        with pytest.raises(IntegrityError):
            s.commit()


def test_dos_cifrados_del_mismo_email_son_distintos_pero_el_hash_es_igual():
    assert gk.cifrar_email("a@x.org") != gk.cifrar_email("a@x.org") and gk.email_hash("a@x.org") == gk.email_hash("a@x.org")


def test_el_hash_depende_de_la_clave(monkeypatch):
    antes = gk.email_hash("a@x.org")
    poner_claves(monkeypatch)
    assert gk.email_hash("a@x.org") != antes


# ------------------------------------------------------------------ rotación de claves
def test_rotar_kem_y_hash_sin_perder_los_emails(engine, monkeypatch, claves):
    with Session(engine) as s:
        svc.crear_usuario(s, "ana@example.org", "Ana")
        svc.crear_usuario(s, "luis@example.org", "Luis")
        s.commit()
        viejo_blob = s.query(Usuario).first().email_cifrado
        poner_claves(monkeypatch, anteriores=claves)                           # claves nuevas; las viejas pasan a _ANTERIORES
        assert svc.email_de(s.query(Usuario).first()) in {"ana@example.org", "luis@example.org"}     # sigue legible
        assert svc.buscar_por_email(s, "ana@example.org") is not None          # y buscable con el hash anterior
        r = svc.rotar_cifrado(s)
        s.commit()
        assert (r.reencriptados, r.rehasheados) == (2, 2)
        assert s.query(Usuario).first().email_cifrado != viejo_blob
        assert svc.rotar_cifrado(s).reencriptados == 0 and svc.rotar_cifrado(s).rehasheados == 0      # idempotente
        monkeypatch.delenv(config.ENV_KEM_PREVIOUS)                            # ya se pueden retirar las anteriores
        monkeypatch.delenv(config.ENV_EMAIL_HASH_PREVIOUS)
        gk.reiniciar()
        assert svc.buscar_por_email(s, "luis@example.org") is not None
        assert {svc.email_de(u) for u in s.query(Usuario)} == {"ana@example.org", "luis@example.org"}


def test_retirar_la_clave_anterior_antes_de_rotar_pierde_los_emails(engine, monkeypatch, claves):
    with Session(engine) as s:
        u = svc.crear_usuario(s, "ana@example.org", "Ana")
        s.commit()
        poner_claves(monkeypatch)                                              # sin _ANTERIORES: error claro, no datos corruptos
        with pytest.raises(gk.ErrorGestorClaves):
            svc.email_de(u)


# ------------------------------------------------------------------ migraciones 0003 y 0004
def alembic_cfg(tmp_path) -> tuple[Config, str]:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg, url


def insertar_en_claro(url, *emails):
    eng = create_engine(url)
    with eng.begin() as conn:
        for email in emails:
            conn.execute(text("INSERT INTO usuarios (id, email, nombre, rol, activo, creado_utc) "
                              "VALUES (:i, :e, 'Ana', 'analista', 1, '2026-11-14 00:00:00')"), {"i": uuid.uuid4().hex, "e": email})
    return eng


def test_migrar_usuarios_existentes_cifra_verifica_y_quita_el_email(tmp_path):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    insertar_en_claro(url, "Ana@Example.org", "luis@example.org")
    command.upgrade(cfg, "0003_email_cifrado")
    eng = create_engine(url)
    with eng.connect() as conn:
        assert "email" in {c["name"] for c in inspect(conn).get_columns("usuarios")}             # aún conviven
        filas = conn.execute(text("SELECT email, email_cifrado, email_hash FROM usuarios ORDER BY email")).fetchall()
        assert all(f.email_cifrado and len(f.email_hash) == 64 for f in filas)
        assert [gk.descifrar_email(f.email_cifrado) for f in filas] == ["ana@example.org", "luis@example.org"]
    command.upgrade(cfg, "head")
    with eng.connect() as conn:
        columnas = {c["name"] for c in inspect(conn).get_columns("usuarios")}
        assert "email" not in columnas and {"email_cifrado", "email_hash"} <= columnas
        assert "example.org" not in repr(conn.execute(text("SELECT * FROM usuarios")).fetchall())
    with Session(eng) as s:
        assert svc.buscar_por_email(s, "ana@example.org") is not None


def test_migrar_sin_usuarios_no_necesita_claves(tmp_path, monkeypatch):
    monkeypatch.delenv(config.ENV_KEM_PRIVATE)
    monkeypatch.delenv(config.ENV_EMAIL_HASH_KEY)
    gk.reiniciar()
    monkeypatch.setenv("K_SERVICE", "x")                                        # producción sin claves del servidor
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "head")
    assert "email_cifrado" in {c["name"] for c in inspect(create_engine(url)).get_columns("usuarios")}


def test_no_se_borra_el_email_si_el_cifrado_no_coincide(tmp_path):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    eng = insertar_en_claro(url, "ana@example.org", "luis@example.org")
    command.upgrade(cfg, "0003_email_cifrado")
    with eng.begin() as conn:                                                   # corrompe: el blob de Ana pasa a ser el de otro email
        conn.execute(text("UPDATE usuarios SET email_cifrado = :b WHERE email = 'ana@example.org'"),
                     {"b": gk.cifrar_email("otro@example.org")})
    with pytest.raises(RuntimeError, match="no coincide"):
        command.upgrade(cfg, "head")
    with eng.connect() as conn:                                                 # nada cambió: el email en claro sigue ahí
        assert "email" in {c["name"] for c in inspect(conn).get_columns("usuarios")}
        assert conn.execute(text("SELECT count(*) FROM usuarios WHERE email IS NOT NULL")).scalar() == 2


def test_no_se_borra_el_email_si_falta_el_cifrado(tmp_path):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0003_email_cifrado")
    eng = insertar_en_claro(url, "ana@example.org")                             # inserción posterior a la copia: sin cifrar
    with pytest.raises(RuntimeError, match="no tiene email cifrado"):
        command.upgrade(cfg, "head")
    assert "email" in {c["name"] for c in inspect(eng).get_columns("usuarios")}


def test_el_downgrade_restituye_el_email_descifrando(tmp_path):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    insertar_en_claro(url, "ana@example.org")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003_email_cifrado")
    eng = create_engine(url)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT email FROM usuarios")).scalar() == "ana@example.org"
    command.downgrade(cfg, "0002_claves")
    assert "email_cifrado" not in {c["name"] for c in inspect(create_engine(url)).get_columns("usuarios")}


# ------------------------------------------------------------------ sin claves del servidor: abortar antes de tocar nada
def columnas(url):
    return {c["name"] for c in inspect(create_engine(url)).get_columns("usuarios")}


@pytest.mark.parametrize("faltan", [(config.ENV_KEM_PRIVATE,), (config.ENV_EMAIL_HASH_KEY,),
                                     (config.ENV_KEM_PRIVATE, config.ENV_EMAIL_HASH_KEY)], ids=["kem", "hash", "ambas"])
def test_0003_con_usuarios_y_sin_claves_aborta_antes_de_tocar_nada(tmp_path, monkeypatch, faltan):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    insertar_en_claro(url, "ana@example.org")
    for nombre in faltan:
        monkeypatch.delenv(nombre)
    gk.reiniciar()
    with pytest.raises(RuntimeError, match="No se ha modificado nada"):
        command.upgrade(cfg, "0003_email_cifrado")
    assert "email_cifrado" not in columnas(url) and "email" in columnas(url)               # esquema intacto
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT email FROM usuarios")).scalar() == "ana@example.org"
    assert not list(tmp_path.glob("**/claves_servidor.json"))                              # y no se generaron claves de desarrollo


def test_0004_sin_claves_aborta_y_no_borra_el_email(tmp_path, monkeypatch):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    insertar_en_claro(url, "ana@example.org")
    command.upgrade(cfg, "0003_email_cifrado")
    monkeypatch.delenv(config.ENV_EMAIL_HASH_KEY)
    gk.reiniciar()
    with pytest.raises(RuntimeError, match="No se ha modificado nada"):
        command.upgrade(cfg, "head")
    assert "email" in columnas(url)


def test_downgrade_de_0004_sin_claves_aborta_y_no_deja_la_tabla_a_medias(tmp_path, monkeypatch):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    insertar_en_claro(url, "ana@example.org")
    command.upgrade(cfg, "head")
    monkeypatch.delenv(config.ENV_KEM_PRIVATE)
    gk.reiniciar()
    with pytest.raises(RuntimeError, match="No se ha modificado nada"):
        command.downgrade(cfg, "0003_email_cifrado")
    assert "email" not in columnas(url) and "email_cifrado" in columnas(url)               # sigue en 0004, intacto


def test_el_downgrade_restituye_el_email_normalizado(tmp_path):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    insertar_en_claro(url, "  Ana@Example.ORG ")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003_email_cifrado")
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT email FROM usuarios")).scalar() == "ana@example.org"      # minúsculas y sin espacios


def test_la_copia_cifrada_funciona_por_lotes(tmp_path):
    cfg, url = alembic_cfg(tmp_path)
    command.upgrade(cfg, "0002_claves")
    insertar_en_claro(url, *[f"usuario{i}@example.org" for i in range(1100)])              # más de dos lotes de 500
    command.upgrade(cfg, "head")
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM usuarios WHERE email_cifrado IS NOT NULL")).scalar() == 1100
        assert conn.execute(text("SELECT count(DISTINCT email_hash) FROM usuarios")).scalar() == 1100

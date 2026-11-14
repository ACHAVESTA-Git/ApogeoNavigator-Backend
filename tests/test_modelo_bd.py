"""Modelo y migraciones: SQLite en memoria/archivo (mismo código que PostgreSQL gracias a los tipos portables)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import models
from app.db.base import Base
from app.db.enums import AccionAuditoria, RolUsuario, TipoCarga

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
HASH = "a" * 64


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def _user(**kw):
    return models.Usuario(email=kw.pop("email", "a@example.org"), nombre="Ana", **kw)


def test_crea_las_cuatro_tablas(engine):
    assert {"usuarios", "sesiones", "cargas_archivo", "eventos_auditoria"} <= set(inspect(engine).get_table_names())


def test_usuario_valores_por_defecto(engine):
    with Session(engine) as s:
        u = _user()
        s.add(u)
        s.commit()
        assert isinstance(u.id, uuid.UUID) and u.rol is RolUsuario.ANALISTA and u.activo is True
        assert u.creado_utc is not None and u.ultimo_login_utc is None


def test_email_unico(engine):
    with Session(engine) as s:
        s.add_all([_user(), _user()])
        with pytest.raises(IntegrityError):
            s.commit()


def test_oauth_unico_por_proveedor(engine):
    with Session(engine) as s:
        s.add_all([_user(email="a@x.org", proveedor_oauth="google", sub_oauth="1"),
                   _user(email="b@x.org", proveedor_oauth="google", sub_oauth="1")])
        with pytest.raises(IntegrityError):
            s.commit()


def test_sesion_se_borra_con_el_usuario(engine):
    with Session(engine) as s:
        u = _user()
        u.sesiones.append(models.Sesion(expira_utc=datetime.now(timezone.utc) + timedelta(hours=1), ip_hash=HASH))
        s.add(u)
        s.commit()
        s.delete(u)
        s.commit()
        assert s.query(models.Sesion).count() == 0


def test_carga_archivo_guarda_firma_binaria(engine):
    with Session(engine) as s:
        s.add(models.CargaArchivo(actor="api-key", tipo=TipoCarga.SITES, nombre_archivo="sites.csv", sha3_256=HASH,
                                  tamano_bytes=10, filas=3, valido=True, total_errores=0, firma_pqc=b"\x00\x01" * 100,
                                  algoritmo_firma="ML-DSA-65"))
        s.commit()
        carga = s.query(models.CargaArchivo).one()
        assert carga.firma_pqc == b"\x00\x01" * 100 and carga.usuario_id is None


def test_evento_detalle_json_y_enum(engine):
    with Session(engine) as s:
        s.add(models.EventoAuditoria(secuencia=1, ts_utc=datetime.now(timezone.utc), actor="api-key", accion=AccionAuditoria.CARGA_GUARDADA,
                                     detalle={"archivo": "sites.csv", "filas": 3}, hash_prev="0" * 64, hash_actual=HASH,
                                     algoritmo_firma="NINGUNO"))
        s.commit()
        ev = s.query(models.EventoAuditoria).one()
        assert ev.id == 1 and ev.accion is AccionAuditoria.CARGA_GUARDADA and ev.detalle["filas"] == 3


def test_hash_prev_unico_impide_bifurcar_la_cadena(engine):
    with Session(engine) as s:
        for i in range(2):
            s.add(models.EventoAuditoria(secuencia=i + 1, ts_utc=datetime.now(timezone.utc), actor="x",
                                         accion=AccionAuditoria.LOGIN, detalle={}, hash_prev="0" * 64,
                                         hash_actual=f"{i}" * 64, algoritmo_firma="NINGUNO"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_evento_sin_firma_exige_declararlo_con_NINGUNO(engine):
    with Session(engine) as s:
        s.add(models.EventoAuditoria(secuencia=1, ts_utc=datetime.now(timezone.utc), actor="x", accion=AccionAuditoria.LOGIN,
                                     detalle={}, hash_prev="0" * 64, hash_actual=HASH))     # ni firma ni NINGUNO
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        s.add(models.EventoAuditoria(secuencia=1, ts_utc=datetime.now(timezone.utc), actor="x", accion=AccionAuditoria.LOGIN,
                                     detalle={}, hash_prev="0" * 64, hash_actual=HASH, firma_pqc=b"x", algoritmo_firma="ML-DSA-65"))
        s.commit()


def test_evento_con_firma_no_puede_declararse_NINGUNO(engine):
    with Session(engine) as s:
        s.add(models.EventoAuditoria(secuencia=1, ts_utc=datetime.now(timezone.utc), actor="x", accion=AccionAuditoria.LOGIN,
                                     detalle={}, hash_prev="0" * 64, hash_actual=HASH, firma_pqc=b"x", algoritmo_firma="NINGUNO"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_restricciones_de_longitud_y_no_negativos(engine):
    with Session(engine) as s:
        s.add(models.EventoAuditoria(secuencia=1, ts_utc=datetime.now(timezone.utc), actor="x", accion=AccionAuditoria.LOGIN,
                                     detalle={}, hash_prev="corto", hash_actual=HASH, algoritmo_firma="NINGUNO"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        s.add(models.CargaArchivo(actor="x", tipo=TipoCarga.SITES, nombre_archivo="sites.csv", sha3_256=HASH, tamano_bytes=-1,
                                  filas=1, valido=True, total_errores=0, firma_pqc=b"x", algoritmo_firma="ML-DSA-65"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_secuencia_unica(engine):
    with Session(engine) as s:
        for i in range(2):
            s.add(models.EventoAuditoria(secuencia=1, ts_utc=datetime.now(timezone.utc), actor="x", accion=AccionAuditoria.LOGIN,
                                         detalle={}, hash_prev=f"{i}" * 64, hash_actual=f"{i + 5}" * 64, algoritmo_firma="NINGUNO"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_borrar_usuario_no_toca_la_auditoria(engine):
    from sqlalchemy import event as sa_event, text
    sa_event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    with Session(engine) as s:
        u = _user()
        s.add(u)
        s.commit()
        s.add(models.EventoAuditoria(secuencia=1, usuario_id=u.id, ts_utc=datetime.now(timezone.utc), actor="x",
                                     accion=AccionAuditoria.LOGIN, detalle={}, hash_prev="0" * 64, hash_actual=HASH,
                                     algoritmo_firma="NINGUNO"))
        s.commit()
        s.delete(u)
        s.commit()
        assert s.query(models.EventoAuditoria).one().usuario_id == u.id    # la fila no cambió


def test_enums_de_la_migracion_coinciden_con_enums_py():
    import importlib.util
    spec = importlib.util.spec_from_file_location("m0001", ROOT / "alembic" / "versions" / "0001_modelo_inicial.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.ROLES == tuple(m.value for m in RolUsuario)
    assert mod.TIPOS == tuple(m.value for m in TipoCarga)
    assert mod.ACCIONES == tuple(m.value for m in AccionAuditoria)


def test_indices_de_auditoria(engine):
    nombres = {ix["name"] for ix in inspect(engine).get_indexes("eventos_auditoria")}
    assert {"ix_eventos_auditoria_ts_utc", "ix_eventos_auditoria_usuario_id", "ix_eventos_auditoria_accion"} <= nombres


def test_migracion_crea_el_mismo_esquema_que_los_modelos(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    assert set(inspect(eng).get_table_names()) >= {"usuarios", "sesiones", "cargas_archivo", "eventos_auditoria"}
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff
    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}


def test_check_de_firma_coincide_entre_modelo_y_migracion():
    import importlib.util
    from app.core import config as c
    spec = importlib.util.spec_from_file_location("m0001", ROOT / "alembic" / "versions" / "0001_modelo_inicial.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.SIGNED_OR_NONE == models.SIGNED_OR_NONE
    assert f"'{c.UNSIGNED_ALGORITHM}'" in mod.SIGNED_OR_NONE

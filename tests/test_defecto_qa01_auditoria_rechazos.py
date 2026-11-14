"""DEF-QA-01: el rechazo de un archivo se audita con los bytes realmente leídos (informe docs/qa)."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core import config as c
from app.db import session as db_session
from app.db.enums import AccionAuditoria as A
from app.db.models import CargaArchivo, EventoAuditoria
from app.main import app
from app.utils import auditoria_util as au
from tests.conftest import upload


def _events():
    with db_session.session_scope() as s:
        return s.query(EventoAuditoria).order_by(EventoAuditoria.secuencia).all()


def _cargas():
    with db_session.session_scope() as s:
        return s.query(CargaArchivo).all()


@pytest.fixture
def api(bd, data_dir, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("IP_HASH_SALT", "sal-de-prueba")
    au.set_firmante()
    return TestClient(app)


@pytest.mark.parametrize("dry_run", [True, False])
def test_rechazo_por_tamano_audita_los_bytes_leidos_y_no_el_hash_vacio(api, dry_run):
    big = b"x" * (c.MAX_FILE_BYTES + 1000)           # pasa el guardia previo (margen de 64 KiB) y se corta al leer
    assert upload(api, "efemerides", big, "efemerides_sitio_uno.csv", dry_run=dry_run).status_code == 413
    (ev,) = _events()
    assert ev.accion is A.CARGA_RECHAZADA
    assert ev.detalle["tamano_bytes"] == c.MAX_FILE_BYTES + 1       # lo leído hasta el corte, no 0
    assert ev.detalle["sha3_256"] is None and ev.detalle[c.KEY_TRUNCADO] is True
    assert ev.detalle["sha3_256"] != hashlib.sha3_256(b"").hexdigest()
    assert _cargas() == []                                          # cargas_archivo exige el hash: no se crea fila


@pytest.mark.parametrize("filename,raw,code", [
    ("efemerides_sitio_uno.csv", "utc,sol_az_deg\n".encode("utf-16"), "E_CODIFICACION"),
    ("efemerides_sitio_uno.csv", b"PK\x03\x04" + bytes(range(256)), "E_TIPO_ARCHIVO"),
    ("nombre_malo.csv", b"utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg\n2026-12-01T00:00Z,1,1,1,1\n", "E_NOMBRE_ARCHIVO"),
])
def test_otros_rechazos_de_archivo_auditan_tamano_y_hash_reales(api, filename, raw, code):
    response = upload(api, "efemerides", raw, filename)
    assert response.status_code in (415, 422) and code in response.text
    (ev,), (carga,) = _events(), _cargas()
    assert ev.accion is A.CARGA_RECHAZADA
    assert ev.detalle["tamano_bytes"] == len(raw) == carga.tamano_bytes
    assert ev.detalle["sha3_256"] == hashlib.sha3_256(raw).hexdigest() == carga.sha3_256
    assert c.KEY_TRUNCADO not in ev.detalle


def test_un_archivo_valido_sigue_auditando_su_hash(api):
    from tests.conftest import fixture_bytes
    assert upload(api, "sites", "sites_limites.csv", "sites.csv").status_code == 200
    (ev,), (carga,) = _events(), _cargas()
    assert ev.detalle["sha3_256"] == hashlib.sha3_256(fixture_bytes("sites_limites.csv")).hexdigest() == carga.sha3_256
    assert c.KEY_TRUNCADO not in ev.detalle


def test_la_cadena_sigue_siendo_valida_con_rechazos_truncados(api):
    assert upload(api, "efemerides", b"x" * (c.MAX_FILE_BYTES + 10), "efemerides_sitio_uno.csv").status_code == 413
    assert upload(api, "sites", "sites_limites.csv", "sites.csv").status_code == 200
    assert au.verificar_cadena().valida

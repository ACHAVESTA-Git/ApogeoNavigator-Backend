import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
API_KEY = "clave-de-prueba"


@pytest.fixture(scope="session")
def pqc_keys() -> tuple[str, str]:
    """Par de claves ML-DSA-65 de prueba (se genera una sola vez por sesión)."""
    from app.security import pqc
    return pqc.generar_par()


class MemoryStore:
    """Almacén de objetos en memoria con generaciones y precondiciones, como Cloud Storage."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects: dict[str, tuple[bytes, int]] = {}
        self._next = 0
        self.list_calls = self.upload_calls = 0
        self.fail = None                     # excepción a lanzar en la próxima operación (simula caídas)
        self.fail_uploads: dict[int, Exception] = {}   # nº de subida (1, 2, 3…) -> excepción: falla SOLO en upload
        self.on_upload = None                # callback antes de cada subida (simula a otro escritor)
        for name, data in (objects or {}).items():
            self._put(name, data)

    def _put(self, name, data):
        self._next += 1
        self.objects[name] = (data, self._next)
        return self._next

    def _maybe_fail(self):
        if self.fail:
            exc, self.fail = self.fail, None
            raise exc

    def list(self):
        from app.services.blobstore import BlobInfo
        self._maybe_fail()
        self.list_calls += 1
        return [BlobInfo(n, g) for n, (_, g) in self.objects.items()]

    def download(self, name):
        self._maybe_fail()
        return self.objects[name][0]

    def upload(self, name, data, *, if_generation_match=None):
        from app.services.blobstore import StoreConflict
        self._maybe_fail()
        attempt = self.upload_calls + 1
        if self.on_upload:
            self.on_upload(name)
        if attempt in self.fail_uploads:
            self.upload_calls += 1
            raise self.fail_uploads[attempt]
        current = self.objects.get(name, (None, 0))[1]
        if if_generation_match is not None and if_generation_match != current:
            raise StoreConflict(name)
        self.upload_calls += 1
        return self._put(name, data)

    def names(self):
        return sorted(self.objects)


@pytest.fixture(autouse=True)
def sin_bucket(monkeypatch):
    """Ninguna prueba toca Cloud Storage: sin almacén salvo que la prueba lo inyecte."""
    from app.services import datastore
    for var in ("DATA_BUCKET", "DATA_BUCKET_PREFIX", "DATA_SEED_DIR", "DATA_REFRESH_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    datastore.set_store(None)
    yield
    datastore.reset()


@pytest.fixture
def data_dir(tmp_path, monkeypatch, pqc_keys) -> Path:
    """Carpeta de datos temporal con un sites.csv válido (sitio_uno, sitio_dos, sitio_tres) y claves PQC de prueba."""
    from app.security import pqc
    shutil.copy(FIXTURES / "sites_valido.csv", tmp_path / "sites.csv")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOAD_API_KEY", API_KEY)
    monkeypatch.setenv("PQC_PRIVATE_KEY", pqc_keys[0])
    monkeypatch.setenv("PQC_PUBLIC_KEY", pqc_keys[1])
    pqc.reiniciar()
    yield tmp_path
    pqc.reiniciar()


@pytest.fixture
def client(data_dir) -> TestClient:
    return TestClient(app)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def upload(client, tipo, content, filename, dry_run=True, key=API_KEY):
    """Sube `content` (bytes o nombre de fixture) como multipart."""
    if isinstance(content, str):
        content = fixture_bytes(content)
    path = f"/api/datos/validar/{tipo}" if dry_run else f"/api/datos/{tipo}"
    headers = {"X-API-Key": key} if key is not None else {}
    return client.post(path, files={"file": (filename, content, "text/csv")}, headers=headers)


def report_of(response) -> dict:
    body = response.json()
    return body["detail"] if "detail" in body else body


def find(report: dict, code: str, fila: int | None = None, columna: str | None = None) -> list[dict]:
    return [
        e for e in report["errores"]
        if e["codigo"] == code
        and (fila is None or e["fila"] == fila)
        and (columna is None or e["columna"] == columna)
    ]


def make_ephem(n=48, step_min=60, start="2026-12-01T00:00", mutate=None) -> bytes:
    """Efemérides sintéticas válidas (Sol suave, Tierra casi fija); `mutate` edita las filas."""
    import math
    from datetime import datetime, timedelta

    t0 = datetime.strptime(start, "%Y-%m-%dT%H:%M")
    rows = []
    for i in range(n):
        t = t0 + timedelta(minutes=step_min * i)
        sun_el = 3 * math.sin(i * step_min / 1440 * 2 * math.pi)
        rows.append([t.strftime("%Y-%m-%dT%H:%MZ"), f"{(i * 7.5) % 360:.3f}", f"{sun_el:.3f}",
                     f"{(180 + i * 0.1) % 360:.3f}", "5.000"])
    if mutate:
        mutate(rows)
    return ("utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg\n"
            + "\n".join(",".join(r) for r in rows) + "\n").encode()


@pytest.fixture
def bd():
    """Base de datos SQLite en memoria configurada como motor global, con la firma restablecida."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.db import session as db_session
    from app.db.base import Base
    from app.db import models  # noqa: F401
    from app.utils import auditoria_util

    from sqlalchemy import event

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    # pysqlite no maneja bien SAVEPOINT ni rollback por defecto: receta oficial de SQLAlchemy para que
    # las transacciones de SQLite se comporten como las de PostgreSQL en estas pruebas.
    @event.listens_for(engine, "connect")
    def _sin_autotransaccion(dbapi_connection, _):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_explicito(conn):
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    db_session.configure(engine)
    yield engine
    db_session.configure(None)
    auditoria_util.set_firmante()

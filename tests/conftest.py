import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
API_KEY = "clave-de-prueba"


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """Carpeta de datos temporal con un sites.csv válido (sitio_uno, sitio_dos, sitio_tres)."""
    shutil.copy(FIXTURES / "sites_valido.csv", tmp_path / "sites.csv")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOAD_API_KEY", API_KEY)
    return tmp_path


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

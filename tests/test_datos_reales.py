"""Caso feliz con los CSV reales de data/ (los aporta el agente de datos)."""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import API_KEY, upload

REAL_DATA = Path(__file__).resolve().parents[1] / "data"
FILES = sorted(REAL_DATA.glob("*.csv"))

pytestmark = pytest.mark.skipif(not (REAL_DATA / "sites.csv").is_file(), reason="no hay data/sites.csv")


@pytest.fixture
def real_client(tmp_path, monkeypatch):
    for f in FILES:
        shutil.copy(f, tmp_path / f.name)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOAD_API_KEY", API_KEY)
    return TestClient(app)


def _tipo(name: str) -> str:
    return "sites" if name == "sites.csv" else "metricas" if name == "metricas.csv" else "efemerides"


@pytest.mark.parametrize("path", FILES, ids=[f.name for f in FILES])
def test_csv_real_es_valido(real_client, path):
    response = upload(real_client, _tipo(path.name), path.read_bytes(), path.name)
    assert response.status_code == 200, response.json()
    assert response.json()["valido"] is True


def test_api_sites_con_datos_reales(real_client):
    sites = real_client.get("/api/sites").json()
    assert len(sites) >= 1
    assert all(isinstance(s["lat_deg"], float) and isinstance(s["lon_este_deg"], float) for s in sites)

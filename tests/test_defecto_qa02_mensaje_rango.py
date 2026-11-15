"""DEF-QA-02: el mensaje de rango máximo muestra dos decimales (informe docs/qa)."""
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config as c
from app.routers import lectura as lectura_router
from app.services import lectura

REAL_DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.mark.skipif(not (REAL_DATA / "sites.csv").is_file(), reason="no hay data/sites.csv")
def test_el_mensaje_de_rango_muestra_dos_decimales(tmp_path, monkeypatch):
    for name in ("sites.csv", "efemerides_slim.csv"):
        shutil.copy(REAL_DATA / name, tmp_path / name)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    lectura.clear_cache()
    mini = FastAPI()
    mini.include_router(lectura_router.router)
    response = TestClient(mini).get("/api/efemerides/slim", params={"desde": "2026-12-01T00:00Z", "hasta": "2027-01-05T01:00Z"})
    assert response.status_code == 422
    assert response.json()["detail"] == "El rango máximo es de 35 días (se pidieron 35.04 días)"


def test_la_plantilla_del_mensaje_de_rango():
    assert c.MSG_QUERY_RANGE.format(max=35, days=62.0) == "El rango máximo es de 35 días (se pidieron 62.00 días)"

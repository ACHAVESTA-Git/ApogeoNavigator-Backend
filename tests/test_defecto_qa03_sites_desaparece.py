"""DEF-QA-03: si sites.csv desaparece entre la comprobación y la lectura, la API responde 503 y no 500 (informe docs/qa)."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import config as c
from app.main import app
from app.services import storage


def test_sites_csv_que_desaparece_entre_la_comprobacion_y_la_lectura_es_503(data_dir, monkeypatch):
    real = Path.read_bytes

    def vanishing(self):
        if self.name == c.SITES_FILENAME:
            raise FileNotFoundError(self)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", vanishing)
    assert (data_dir / c.SITES_FILENAME).is_file() and storage.read_sites() is None
    response = TestClient(app, raise_server_exceptions=False).get("/api/sites")
    assert response.status_code == 503 and response.json() == {"detail": c.MSG_NO_SITES}

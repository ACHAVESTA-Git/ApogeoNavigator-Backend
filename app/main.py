import csv
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

# En Docker: DATA_DIR=/data (volumen). En local sin Docker: ../data junto al proyecto.
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))

app = FastAPI(title="Apogeo API", version="0.1.0")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/sites")
def sites() -> list[dict]:
    path = DATA_DIR / "sites.csv"
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"No se encontró {path.name}")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

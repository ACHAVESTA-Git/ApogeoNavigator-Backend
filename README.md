# Apogeo · Backend (FastAPI)

API que sirve el catálogo de sitios y (próximamente) métricas y efemérides. Se despliega en Cloud Run.

## Endpoints
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/sites` → lista de `sites.csv`

## Variables de entorno
| Variable | Defecto | Uso |
|---|---|---|
| `PORT` | `8080` | Puerto de escucha (lo fija Cloud Run) |
| `DATA_DIR` | `../data` | Carpeta con los CSV |

## Ejecutar sin Docker
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=../data uvicorn app.main:app --reload --port 8080
```

## Docker
```bash
docker build -t apogeo-backend .
docker run --rm -p 8080:8080 -v "$PWD/../data:/data:ro" -e DATA_DIR=/data apogeo-backend
```
Docs interactivas: http://localhost:8080/docs

# Apogeo Navigator · Backend

API de **Lunar Landing Site Navigator**: sirve el catálogo de sitios y, próximamente, efemérides y métricas (proxy a JPL Horizons). Proyecto de Apogeo Team para NASA Space Apps Challenge 2026.

## Stack
Python · FastAPI · Uvicorn · Docker · Cloud Run (producción) · Cloud Storage (datos).

## Endpoints
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/sites` → lista de `sites.csv`

Previstos: `/api/efemerides/{site_id}`, `/api/metricas`, `/api/puestas`, `/api/horizons`.

## Variables de entorno
| Variable | Defecto | Uso |
|---|---|---|
| `PORT` | `8080` | Puerto de escucha (lo fija Cloud Run) |
| `DATA_DIR` | `../data` | Carpeta con los CSV |

## Ejecutar en local
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=../data uvicorn app.main:app --reload --port 8080
```
Docs interactivas: http://localhost:8080/docs

## Docker
```bash
docker build -t apogeo-backend .
docker run --rm -p 8080:8080 -v "$PWD/../data:/data:ro" -e DATA_DIR=/data apogeo-backend
```

## Repositorios del proyecto
- Frontend: https://github.com/ACHAVESTA-Git/ApogeoNavigator-Frontend
- Backend: https://github.com/ACHAVESTA-Git/ApogeoNavigator-Backend

Los CSV de datos (`sites.csv`, `efemerides_<site_id>.csv`, `metricas.csv`) viven en la carpeta `data/` del espacio de trabajo y, en producción, en Cloud Storage.

## Contribuir
Lee [CONTRIBUTING.md](CONTRIBUTING.md): ramas, commits con ID de la HU y Pull Requests.

## Licencia
[MIT](LICENSE) © 2026 Apogeo Team.

Los datos provienen de fuentes de la NASA (JPL Horizons, NSSDC, Moon Trek). NASA no respalda este proyecto.

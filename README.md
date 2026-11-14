# Apogeo Navigator · Backend

API de **Lunar Landing Site Navigator**: sirve el catálogo de sitios y, próximamente, efemérides y métricas (proxy a JPL Horizons). Proyecto de Apogeo Team para NASA Space Apps Challenge 2026.

## Stack
Python · FastAPI · Uvicorn · Docker · Cloud Run (producción) · Cloud Storage (datos).

## Endpoints
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/sites` → catálogo de `sites.csv` con los números como número (503 si el archivo falta o no cumple el estándar)
- `POST /api/datos/validar/{tipo}` → dry-run: valida un CSV y devuelve el informe sin guardar
- `POST /api/datos/{tipo}` → valida y guarda de forma atómica, con copia de la versión anterior en `data/_historial/`

`tipo` ∈ `sites` · `efemerides` · `metricas`. Previstos: `/api/efemerides/{site_id}`, `/api/metricas`, `/api/puestas`, `/api/horizons`.

### Carga de CSV
Las reglas están en [`docs/plan/estandar-csv.md`](../docs/plan/estandar-csv.md) (fuente de verdad) y en `app/core/config.py`. Subida multipart con la cabecera `X-API-Key` (valor de `UPLOAD_API_KEY`; sin clave configurada o con clave errónea responde 401).
```bash
curl -H "X-API-Key: $UPLOAD_API_KEY" -F "file=@sites.csv" http://localhost:8080/api/datos/validar/sites
curl -H "X-API-Key: $UPLOAD_API_KEY" -F "file=@efemerides_apollo11.csv" http://localhost:8080/api/datos/efemerides
```
El nombre del archivo debe ser `sites.csv`, `metricas.csv` o `efemerides_<site_id>.csv`. Respuestas: 200 (válido), 401, 413 (`E_TAMANO`), 415 (`E_TIPO_ARCHIVO`) y 422 (resto de errores), con el informe en `detail`:
```json
{"detail": {"archivo": "sites.csv", "tipo": "sites", "valido": false, "total_filas": 3, "total_errores": 1,
  "errores": [{"fila": 2, "columna": "lat_deg", "codigo": "E_RANGO", "mensaje": "Debe estar entre -90 y 90", "valor": "91"}],
  "advertencias": []}}
```
Notas de implementación: una fecha con forma correcta pero imposible (`2026-02-30T00:00Z`) da `E_TIPO`; una con forma incorrecta o zona distinta de `Z` da `E_FORMATO`. Los errores de celda se informan primero; las reglas de coherencia entre filas y con `sites.csv` solo se evalúan cuando no hay errores de celda.

## Variables de entorno
| Variable | Defecto | Uso |
|---|---|---|
| `PORT` | `8080` | Puerto de escucha (lo fija Cloud Run) |
| `DATA_DIR` | `data/` junto a `app/` | Carpeta con los CSV |
| `UPLOAD_API_KEY` | vacía (carga cerrada) | Clave para `/api/datos/**` |

## Estructura
```
app/core/        config.py (constantes) y security.py (X-API-Key)
app/utils/       validadores genéricos: texto, decimal, fecha UTC, enum/regex/url, lector CSV
app/validation/  specs.py (esquemas declarativos), engine.py (validador), coherence.py (reglas por tipo)
app/schemas/     modelos Pydantic (informe, Site)
app/services/    lectura de sites.csv y guardado atómico
app/routers/     datos.py
tests/           pytest; fixtures inválidos en tests/fixtures/
```

## Ejecutar en local
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
DATA_DIR=data UPLOAD_API_KEY=cambia-esto uvicorn app.main:app --reload --port 8080
```
Pruebas: `pytest`. Docs interactivas: http://localhost:8080/docs

## Docker
```bash
docker build -t apogeo-backend .
docker run --rm -p 8080:8080 -v "$PWD/data:/data" -e DATA_DIR=/data -e UPLOAD_API_KEY=cambia-esto apogeo-backend
```

## Repositorios del proyecto
- Frontend: https://github.com/ACHAVESTA-Git/ApogeoNavigator-Frontend
- Backend: https://github.com/ACHAVESTA-Git/ApogeoNavigator-Backend

Los CSV de datos (`sites.csv`, `efemerides_<site_id>.csv`, `metricas.csv`) viven en la carpeta `data/` de este repositorio y, en producción, en Cloud Storage.

## Contribuir
Lee [CONTRIBUTING.md](CONTRIBUTING.md): ramas, commits con ID de la HU y Pull Requests.

## Licencia
[MIT](LICENSE) © 2026 Apogeo Team.

Los datos provienen de fuentes de la NASA (JPL Horizons, NSSDC, Moon Trek). NASA no respalda este proyecto.

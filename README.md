# Lunar Landing Site Navigator · Backend (API)

**Where are the Sun and the Earth in the sky of a lunar landing site, hour by hour?** This is the API behind *Lunar Landing Site Navigator*, built by **Apogeo Team** for the NASA Space Apps Challenge 2026. It serves NASA/JPL Horizons ephemerides for ten landing sites, computes sunlight and Earth-visibility metrics from them, and lets a trusted operator upload new data with strict validation and a tamper-evident trail.

- Frontend (React): https://github.com/ACHAVESTA-Git/ApogeoNavigator-Frontend
- Live demo, video and challenge submission: links will be added here once published.
- [Quick start](#quick-start) · [API](#api) · [NASA data and sources](#nasa-data-and-sources) · [Integrity and security](#integrity-and-security) · [Limitations](#limitations) · [Tests](#tests) · [AI usage declaration](#ai-usage-declaration) · [License](#license) · [Resumen en español](#resumen-en-español)

## Why it matters
Near the lunar south pole the Sun skims the horizon and the Earth hovers around it, so a small change of place or date can leave a lander without power or without a direct link to Earth. The tools that hold the data are powerful but slow for comparing scenarios. This API answers those questions in one request. Two examples from the December 2026 data (ideal horizon, see [Limitations](#limitations)):

| Site | Sun above horizon | Highest Sun | Earth above horizon |
|---|---|---|---|
| Shackleton crater (south pole) | 100 % (ideal-horizon artefact) | **1.74°** | **39 %** |
| Chang'e 4 (far side) | 49.7 % | 46.0° | **0 %** |

At Shackleton the Sun never rises above 1.74° (about 88° at Apollo 11), and on the far side the Earth is never visible, so a relay satellite is needed.

## Architecture
```
 Frontend (React, Cloudflare Pages)
        │  HTTPS, CORS
        ▼
 FastAPI (this repo) ──── data/ CSV: sites, hourly ephemerides, monthly metrics
        │                       ▲ signed manifest (ML-DSA-65)
        │                       └ production: Cloud Storage bucket is the source of truth
        └──── PostgreSQL: hash-chained audit log, upload records, user keys (optional)
```
- **Reads** use a local replica of `data/`; the metrics are recomputed from the ephemerides and compared with the reference `metricas.csv`.
- **Uploads** (`/api/datos/**`, API key required) are validated against a declarative schema, saved atomically, signed into a manifest and audited.
- **Errors** are JSON `{"detail": ...}` everywhere, handled in one place (`app/core/errors.py`): domain errors keep their status, a database or bucket outage is a `503`, and anything unexpected is a generic `500` whose trace goes to the log, never to the client. Native FastAPI validation errors (`422` with a list) are left as they are.
- **Production** target: Cloud Run + Cloud SQL + Cloud Storage on Google Cloud (not deployed yet). The image runs as a non-root user and ships a seed copy of `data/`.

```
app/core/        config.py (all constants), security, CORS, upload guard
app/utils/       generic validators (text, decimal, UTC date, enum/regex/URL) and the safe CSV reader
app/validation/  declarative column specs, the validation engine and per-file coherence rules
app/services/    ephemeris/metrics reading, atomic storage, Cloud Storage replica, users
app/security/    ML-DSA-65 signing, signed data manifest, per-user key manager
app/routers/     datos, lectura, auditoria, seguridad, admin_claves
app/db/          SQLAlchemy 2.0 models and sessions; alembic/ holds the migrations
scripts/         data generation (scripts/datos) and key/manifest tools (scripts/pqc)
```

## Quick start
Python 3.12 or newer. No database, bucket or keys are needed to read the data.

```bash
git clone https://github.com/ACHAVESTA-Git/ApogeoNavigator-Backend && cd ApogeoNavigator-Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
DATA_DIR=data uvicorn app.main:app --port 8080
```
Open http://localhost:8080/docs for the interactive API documentation. To see it with the interface, start the [frontend](https://github.com/ACHAVESTA-Git/ApogeoNavigator-Frontend) in a second terminal; it proxies `/api` to port 8080.

With Docker (the image already contains the data):
```bash
docker build -t apogeo-backend .
docker run --rm -p 8080:8080 apogeo-backend
```

## API
Main endpoints (all `GET`, no authentication). Times are UTC (`YYYY-MM-DDTHH:MMZ`).

| Endpoint | What it returns |
|---|---|
| `/api/health` | `{"status":"ok"}` |
| `/api/sites` | The ten landing sites with coordinates and source URL |
| `/api/efemerides/{site_id}?desde=&hasta=` | Hourly Sun and Earth azimuth and elevation (up to 35 days per request) |
| `/api/metricas?site_id=&desde=&hasta=` | Sunlight and Earth-visibility metrics and the traffic light; repeat `site_id` to compare 1 to 3 sites |
| `/api/puestas/{site_id}?desde=` | Next sunset/sunrise and Earth-set/Earth-rise from a given moment |
| `/api/seguridad/clave-publica`, `/api/seguridad/manifiesto` | Public signing key and signed manifest of the data (see [Integrity](#integrity-and-security)) |

```bash
curl "http://localhost:8080/api/metricas?site_id=shackleton&desde=2026-12-01T00:00Z&hasta=2027-01-01T00:00Z"
```
```json
{"resultados":[{"site_id":"shackleton","inicio_utc":"2026-12-01T00:00Z","fin_utc":"2027-01-01T00:00Z",
 "horas_sol":744.0,"pct_sol":100.0,"sol_el_max_deg":1.7385,"sol_el_media_deg":1.2929,
 "horas_tierra_visible":291.0,"pct_tierra":39.11,
 "semaforo":{"energia":"verde","comunicacion":"rojo","global":"rojo"},
 "avisos":[{"codigo":"sensible_al_relieve","mensaje":"..."}],"origen":"calculado",
 "referencia":{"coincide":true}}]}
```
```bash
curl "http://localhost:8080/api/puestas/shackleton?desde=2026-12-01T00:00Z"   # next sunset: 2027-01-27T11:00Z
```
Field names and some texts (site names, notes, warning messages) are currently in Spanish.

Protected endpoints (header `X-API-Key`): `POST /api/datos/validar/{tipo}` (dry run) and `POST /api/datos/{tipo}` (validate and save) with `tipo` = `sites`, `efemerides` or `metricas`; `GET /api/auditoria` and `/api/auditoria/verificar`; `/api/admin/claves/**` (per-user key manager). Invalid files return `422` with a per-row report; there are 22 validation error codes (empty file, encoding, header, range, formula injection, time-step, physical jumps, unknown site, size, and so on).

## NASA data and sources
All sunlight and Earth-visibility figures come from **NASA/JPL Horizons** (https://ssd.jpl.nasa.gov/horizons/): hourly Sun and Earth azimuth and elevation seen from ten locations on the Moon, from 2026-12-01 to 2027-06-01 (DE441 ephemerides, observer `coord@301`, no refraction). Site coordinates come from **NSSDC** (Apollo 11, 15, 17), **LROC** (Shackleton, Blue Ghost 1, Chandrayaan-3, Chang'e 4 and 6, SLIM) and **NASA/LRO** (IM-1 Odysseus). Every site row carries its source URL.

| Source | Used for |
|---|---|
| [JPL Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html) | All ephemerides (and the metrics derived from them) |
| [NSSDC lunar landing sites](https://nssdc.gsfc.nasa.gov/planetary/lunar/lunar_sites.html) | Apollo landing coordinates |
| [LROC (Lunar Reconnaissance Orbiter Camera)](https://www.lroc.asu.edu/) | Positions of recent landers measured on LRO images |
| [NASA/LRO: LRO images Intuitive Machines' Odysseus lander](https://www.nasa.gov/missions/lro/nasas-lro-images-intuitive-machines-odysseus-lander/) | IM-1 Odysseus coordinates |

Moon Trek, QuickMap and the PGDA products were **not** used as data. Exact queries, per-site sources, exclusions and findings: [`docs/datos/informe-fuentes.md`](docs/datos/informe-fuentes.md) (English first, with the original Spanish report below). NASA does not endorse or sponsor this project.

## Integrity and security
The goal is that anyone can check that the data they are shown have not been altered, and that every change to the data is on record. This is **tamper evidence, not tamper prevention**.

- **Validation:** every upload passes a declarative schema plus coherence rules (time ordering and step, physical limits on elevation changes, references to known sites, percentage consistency). Nothing is saved if anything fails.
- **Signed data manifest:** `data/manifest.json` lists the size and SHA3-256 of every CSV and is signed with **ML-DSA-65 (NIST FIPS 204)**, a post-quantum signature scheme, using the `cryptography` library. It is regenerated on every valid upload; if it cannot be signed, nothing is saved. The script `scripts/pqc/verificar_manifiesto.py` verifies it independently (it imports no project code):
  ```bash
  pip install "cryptography>=47"
  python scripts/pqc/verificar_manifiesto.py \
    --manifiesto https://<host>/api/seguridad/manifiesto \
    --clave-publica https://<host>/api/seguridad/clave-publica \
    --huella <OFFICIAL_FINGERPRINT> --datos ./data
  ```
  The expected output is `CORRECTO: firma ML-DSA-65 válida; 12 archivos comprobados`. The fingerprint must come from a channel other than the server (this README). Official public-key fingerprint: to be published here when the production keys are generated.
- **Audit log:** with a database configured, every validation and upload is recorded as an event whose hash includes the previous one (SHA3-256) and is signed with ML-DSA-65. `GET /api/auditoria/verificar` checks the chain. On PostgreSQL a trigger blocks `UPDATE`, `DELETE` and `TRUNCATE` on the log. Deleting the last events or restarting the chain is **not** detectable without an external anchor (the head hash is written to the logs). IPs are never stored, only an HMAC with a secret salt.
- **Personal data:** user emails are stored encrypted (ML-KEM-1024, FIPS 203, with AES-GCM) and searched by an HMAC. Login is not implemented yet.
- **Fail closed in production:** if signing, the bucket or the database is unavailable, an upload is refused instead of being saved without evidence.

Post-quantum signatures are used here for their long-term integrity guarantees on our own files. This does not mean that NASA uses them, and the implementation has not been independently audited. Details: [`docs/bd/modelo-datos.md`](docs/bd/modelo-datos.md) (Spanish).

## Limitations
- **Ideal horizon.** Horizons models a smooth Moon with no terrain. Shackleton's centre shows 100 % Sun in December although the real crater floor is permanently shadowed. Results are flagged as *relief sensitive* when the Sun never exceeds 3°.
- Six months of ephemerides (not a full year) and ten locations. The Artemis III candidate regions are not included because NASA has not published their coordinates.
- The traffic-light thresholds (energy: green from 45 %, yellow from 25 %; communications: green from 70 %, yellow from 40 %) are the project's own choice, not an official NASA criterion.
- The API does not query Horizons live: all figures are computed from the precomputed ephemerides in `data/`.
- Not deployed to production yet; the production architecture above is implemented and tested locally.

## Configuration
Everything is configured with environment variables; see [`.env.example`](.env.example) for the complete, commented list. The most relevant ones:

| Variable | Purpose |
|---|---|
| `DATA_DIR` | Folder with the CSV files (default `data/`) |
| `UPLOAD_API_KEY`, `ADMIN_API_KEY` | API keys for uploads/audit and for the key manager (empty = closed) |
| `DATABASE_URL` | PostgreSQL URL; empty disables the database and the audit log |
| `DATA_BUCKET` | Cloud Storage bucket with the data; **required in production** |
| `PQC_PRIVATE_KEY`, `PQC_PUBLIC_KEY` | ML-DSA-65 signing keys (required in production) |
| `CORS_ORIGINS`, `CORS_ORIGIN_REGEX` | Allowed browser origins (no wildcard) |

## Tests
```bash
pytest                                  # more than 650 tests; no database, bucket or network needed
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/apogeo_test pytest tests/test_postgres.py
```
`tests/test_contrato_congelado.py` freezes the HTTP contract the frontend depends on (status codes, error texts and the shape of every JSON response, plus the OpenAPI operations) against `tests/contrato/snapshots.json`. A refactor must keep it green; if a contract change is deliberate, regenerate with `UPDATE_CONTRATO=1 pytest tests/test_contrato_congelado.py` and review the snapshot diff in the pull request.

The second command runs the PostgreSQL-specific tests (immutable audit trigger, migrations, concurrency) against a throwaway database whose name must contain `test`; they are skipped otherwise.

## AI usage declaration
> **To be completed by the project owner.** The final declaration text, as required by the Space Apps guidelines, will be placed here and mirrored in the challenge submission form.

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md) (in Spanish): branch flow, commit conventions and pull requests.

## License
[MIT](LICENSE) © 2026 Apogeo Team. The data come from NASA sources (see above). **NASA does not endorse or sponsor this project.**

## Resumen en español
**Lunar Landing Site Navigator (backend)** es la API de la app de Apogeo Team para el NASA Space Apps Challenge 2026. Sirve efemérides del Sol y de la Tierra vistas desde diez sitios de alunizaje (datos de **JPL Horizons**; coordenadas de **NSSDC**, **LROC** y **nasa.gov/LRO**; Moon Trek, QuickMap y PGDA **no** se usaron como datos), calcula horas de sol, visibilidad de la Tierra y un semáforo de viabilidad, y permite cargar datos nuevos con validación estricta, un manifiesto firmado con **ML-DSA-65 (FIPS 204)** y una auditoría encadenada que detecta manipulaciones (no las impide).

- **Arrancar:** `pip install -r requirements-dev.txt` y `DATA_DIR=data uvicorn app.main:app --port 8080`; documentación en `/docs`. Con Docker: `docker build -t apogeo-backend .` y `docker run --rm -p 8080:8080 apogeo-backend`.
- **Limitaciones:** horizonte ideal (sin relieve), seis meses de datos, diez sitios, umbrales del semáforo propios del proyecto, aún sin desplegar en producción.
- **Pruebas:** `pytest` (más de 650). **Licencia:** MIT. La NASA no respalda este proyecto.
- Más documentación: `docs/datos/informe-fuentes.md` (en inglés, con el original en español) y, solo en español, `docs/bd/modelo-datos.md` y `CONTRIBUTING.md`.

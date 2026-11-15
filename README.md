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
- **Errors** are JSON `{"detail": ..., "error": {...}}` everywhere, built in one place (`app/core/errors.py` and the catalogue in `app/core/mensajes.py`): domain errors keep their status, a database or bucket outage is a `503`, and anything unexpected is a generic `500` whose trace goes to the log, never to the client. See [Errors](#errors).
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

### Local development keys with Docker Compose
Without `PQC_PRIVATE_KEY` and `PQC_KEM_*`, development generates a local ML-DSA signing pair and an ML-KEM pair in `PQC_DEV_KEY_DIR` (`/srv/.pqc_dev` in the image). The compose file at the workspace root mounts a named volume there (`pqc_dev_keys`, owned by the `app` user, uid 10001, directory mode `0700`), so the keys survive `docker compose up --build`. This matters because `data/manifest.json` also persists: with new keys the manifest fingerprint would no longer match (`/api/analisis` would report `firmado: false`), and emails already encrypted with the old KEM key could not be decrypted.

To rotate the development keys, delete the volume and the local manifest, then start again:
```bash
docker compose down
docker volume rm $(docker volume ls -q --filter name=pqc_dev_keys)
rm ApogeoNavigator-Backend/data/manifest.json
docker compose up --build
```
On startup without a bucket (local, never in production) the backend signs `manifest.json` with the development keys when it is missing. To regenerate it by hand in a running container (`scripts/pqc/` is not shipped in the image):
```bash
docker compose exec backend python -c "from app.security import pqc; from app.core import config as c; d=c.get_data_dir(); pqc.escribir_manifiesto(d, pqc.generar_manifiesto(d))"
```
Rotating makes any email encrypted with the old development KEM key unreadable. In production the keys come from Secret Manager and this volume is not used.

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

### Login and roles
`POST /api/auth/login` (e-mail and password), `POST /api/auth/logout` and `GET /api/auth/me` (id, name, role and the fingerprints of the user's active public keys; **never** private keys). The session is an opaque token in an `httpOnly`, `SameSite=Strict` cookie named `__session` (`Secure` in production; Firebase Hosting only forwards that name) that lasts 8 hours; only its hash is stored, and passwords are hashed with Argon2id. Failed attempts are limited per e-mail and per IP (429 with `Retry-After`) and audited. `POST /api/auth/login` and the requests that change data and are authenticated by cookie must send `X-Requested-With` (any value) or a trusted `Origin` (CSRF). Roles: CSV upload accepts `analista`, `revisor` and `admin`; the key manager only `admin` (403 otherwise). The `X-API-Key` of scripts keeps working. Google OAuth is future work. The image sets `MALLOC_ARENA_MAX=2` on purpose: glibc keeps one malloc arena (about 19 MiB with Argon2id) per thread, and without the cap 30 simultaneous logins peaked above 512 MB; with it the same test stays around 72 MB. Keep it if you build your own image.

### Test users (local demo)
`python -m app.cli.seed_usuarios` (in Docker: `docker compose exec backend python -m app.cli.seed_usuarios`; `scripts/seed_usuarios.sh` is only a local shortcut and is not shipped in the image) creates three accounts, `analista@apogeo.test`, `revisor@apogeo.test` and `admin@apogeo.test` (`.test` is a reserved domain), each with a random password and its two post-quantum key pairs (ML-KEM-1024 and ML-DSA-65). The server keeps only the public keys. The passwords and the private keys are delivered **once**, in a credentials file with mode `600` (`.seed_credentials.local`-style path: `.seed_credenciales.local` by default, `SEED_CREDENTIALS_FILE` or `--salida` to change it; `/srv/.seed/credenciales.local` in the image). They are never printed or logged, and the file is git- and docker-ignored. Read it in the container with `docker compose exec backend cat /srv/.seed/credenciales.local`.

The command is idempotent (it only creates the missing accounts and never touches existing ones). In production it refuses to run unless you pass `--forzar-produccion`; there the credentials file is meant to be moved to Secret Manager and deleted. It needs the database with the migrations applied.

Protected endpoints (header `X-API-Key`, or a session with the right role): `POST /api/datos/validar/{tipo}` (dry run) and `POST /api/datos/{tipo}` (validate and save) with `tipo` = `sites`, `efemerides` or `metricas`; `GET /api/auditoria` and `/api/auditoria/verificar`; `/api/admin/claves/**` (per-user key manager). Invalid files return `422` with a per-row report; there are 22 validation error codes (empty file, encoding, header, range, formula injection, time-step, physical jumps, unknown site, size, and so on).

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

## Errors
Nobody should see a bare `500` or `404`. Every error response says **what failed**, **why** and **what to do**, in the API and in the server console.

```json
{
  "detail": "El sitio no existe en sites.csv",
  "error": {
    "codigo": "SITIO_NO_ENCONTRADO", "estado": 404, "titulo": "Sitio no encontrado",
    "mensaje": "No hay ningún sitio con el identificador «shakleton».",
    "accion": "Revisa el identificador o elige un sitio del catálogo (/api/sites).",
    "request_id": "9f2c1e7a4b", "ts_utc": "2026-11-15T04:12:33Z"
  }
}
```
- `detail` keeps its original value and shape (a text, FastAPI's validation list, or the CSV report of a rejected upload); the `error` object is added next to it. `codigo` is stable (the frontend can translate it); `titulo`, `mensaje` and `accion` are user-facing Spanish texts from a single catalogue, [`app/core/mensajes.py`](app/core/mensajes.py).
- **Every** response, successful or not, carries an `X-Request-ID` header (a client-supplied one is reused if it is 8-64 characters of letters, digits, `.`, `_`, `-`; otherwise a new one is generated). The same id is in `error.request_id` and in the log line, so a report can be traced end to end.
- Nothing internal is ever exposed: no stack traces, SQL, file paths, keys or personal data. Those go to the server log, linked by `request_id`. Only identifier-looking values (letters, digits, `_`, `-`) are quoted back from the request.
- Console: one readable line per request, `2026-11-15T04:12:33Z INFO  9f2c1e7a4b GET  /api/efemerides/shakleton → 404 SITIO_NO_ENCONTRADO (3 ms) | Revisa el identificador…`. 2xx and 3xx are INFO, 401 and 404 are INFO, other 4xx are WARNING and 5xx are ERROR, with the full trace in the line above, linked by `request_id`. The query string and headers are never logged. Set `LOG_FORMAT=texto|json` (`json` for Cloud Run / Cloud Logging) and `LOG_LEVEL`.

**Base catalogue (by status)**

| Status | `codigo` | Title | What to do |
|---|---|---|---|
| 400 | `PETICION_INVALIDA` | Petición inválida | Revisa los datos enviados. |
| 401 | `NO_AUTORIZADO` | Falta autorización | Introduce una clave válida (X-API-Key). |
| 403 | `PROHIBIDO` | Sin permiso | Usa una clave con permiso para esta acción. |
| 404 | `NO_ENCONTRADO` | No encontrado | Revisa la dirección o el identificador. |
| 405 | `METODO_NO_PERMITIDO` | Método no permitido | Usa el método indicado en /docs. |
| 409 | `CONFLICTO` | Conflicto | Los datos cambiaron mientras tanto; recarga y reintenta. |
| 413 | `ARCHIVO_DEMASIADO_GRANDE` | Archivo demasiado grande | Máximo 5 MB y 10 000 filas; divide el archivo. |
| 415 | `TIPO_NO_SOPORTADO` | Tipo de archivo no soportado | Sube un archivo .csv en UTF-8. |
| 422 | `DATOS_INVALIDOS` | Datos inválidos | Corrige los campos indicados y vuelve a intentarlo. |
| 429 | `DEMASIADAS_PETICIONES` | Demasiadas peticiones | Espera unos segundos y reintenta. |
| 500 | `ERROR_INTERNO` | Error inesperado | Reintenta; si persiste, comparte el código de seguimiento. |
| 502 | `SERVICIO_EXTERNO_FALLO` | Fallo de un servicio externo | Reintenta en unos minutos. |
| 503 | `SERVICIO_NO_DISPONIBLE` | Servicio no disponible | Reintenta en unos minutos (datos, BD o almacenamiento caídos). |
| 504 | `TIEMPO_AGOTADO` | Tiempo de espera agotado | Reduce el rango de fechas o reintenta. |

**Domain codes** (more specific than the base code of their status)

| Status | `codigo` | Title | What to do |
|---|---|---|---|
| 401 | `CLAVE_INVALIDA` | Clave inválida o ausente | Envía una clave válida en la cabecera X-API-Key. |
| 404 | `CLAVE_NO_ENCONTRADA` | Clave no encontrada | Revisa el identificador de la clave. |
| 404 | `EFEMERIDES_NO_ENCONTRADAS` | Sin efemérides | Elige otro sitio o carga sus efemérides. |
| 404 | `MANIFIESTO_NO_GENERADO` | Aún no hay manifiesto | Se genera con la primera carga válida; vuelve a intentarlo después. |
| 404 | `SITIO_NO_ENCONTRADO` | Sitio no encontrado | Revisa el identificador o elige un sitio del catálogo (/api/sites). |
| 404 | `TIPO_DATOS_DESCONOCIDO` | Tipo de datos desconocido | Usa sites, efemerides o metricas. |
| 404 | `USUARIO_NO_ENCONTRADO` | Usuario no encontrado | Revisa el identificador del usuario. |
| 409 | `CLAVE_DUPLICADA` | Clave ya activa | Revócala o rótala en lugar de generar otra. |
| 409 | `CLAVE_YA_REVOCADA` | Clave ya revocada | No hace falta revocarla de nuevo; rota o genera una nueva. |
| 409 | `USUARIO_INACTIVO` | Usuario desactivado | Activa al usuario antes de emitirle claves. |
| 422 | `CSV_INVALIDO` | CSV inválido | Corrige las filas indicadas en el informe (detail) y vuelve a subirlo. |
| 422 | `RANGO_DEMASIADO_LARGO` | Rango demasiado largo | Pide un rango de 35 días como máximo y consulta por tramos. |
| 422 | `RANGO_FECHAS_INVALIDO` | Rango de fechas inválido | Usa fechas UTC con el formato AAAA-MM-DDTHH:MMZ y que «hasta» sea posterior a «desde». |
| 503 | `ALMACEN_NO_DISPONIBLE` | Almacenamiento no disponible | Reintenta en unos minutos. |
| 503 | `BD_NO_DISPONIBLE` | Base de datos no disponible | Reintenta en unos minutos; si persiste, avisa al equipo con el código de seguimiento. |
| 503 | `DATOS_NO_DISPONIBLES` | Datos no disponibles | Reintenta en unos minutos; si persiste, avisa al equipo con el código de seguimiento. |
| 503 | `FIRMA_NO_DISPONIBLE` | Firma no disponible | Avisa al equipo con el código de seguimiento: faltan las claves de firma. |

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

# Changelog

All notable changes to the Apogeo backend, grouped by area. Numbers refer to pull requests in
[ApogeoNavigator-Backend](https://github.com/ACHAVESTA-Git/ApogeoNavigator-Backend). The first version of several areas was
committed straight to `dev` before the pull-request rule started on 14 Nov 2026 at 16:45; those have no number.

## Release 2026-11-15 (API 0.2.0, Space Apps 2026 submission)

### NASA data
- Ephemerides of ten landing sites from NASA/JPL Horizons (observer on the Moon, no refraction), six months of hourly data (#3).
- Sunlight threshold for the energy traffic light set to 45 % (#1); accents and agency fixes in the site catalogue (#2, #26); low-severity QA defects (#17); data scripts use shared constants (#7).
- Sources and credits report, in English (#15); IM-1 source link in the README (#23).

### Read API
- `GET /api/sites`, `/api/efemerides/{site_id}`, `/api/metricas` (comparison of 1 to 3 sites) and `/api/puestas/{site_id}`, with a traffic light for energy and communication; `/api/health`.
- Live query for custom coordinates against JPL Horizons, cached, rate limited (429 with `Retry-After`) and bounded (#24, #29).
- Typed response models (DTOs) for health, key and read endpoints (#21, #22) and an HTTP contract frozen by tests against the frontend types (#16).
- OpenAPI in English with descriptions, tags and real examples (#20).

### Simulation
- Sky simulation: real data inside the range and a statistical projection beyond it, computed with Polars (#13).
- Per-point uncertainty from validation against Horizons, with a warning when the reference comes from the seed data (#28).

### Analysis transparency
- `GET /api/analisis/{site_id}`: how a site's data is analysed, step by step (input origin and quality, fit, validation, limits) (#25, #33).

### CSV upload
- `POST /api/datos/validar/{tipo}` (dry run) and `POST /api/datos/{tipo}` (validate and save atomically, keeping the previous version in `_historial/`); a declarative validation engine with 22 error codes; size and row limits (#8).
- The upload guard authenticates for real and counts bytes even without `Content-Length` (#39).
- Data stored in Cloud Storage with a local replica, generation preconditions and a non-root image (#6).

### Audit
- Hash-chained (SHA3-256), ML-DSA-65-signed audit log with an immutability trigger on PostgreSQL; `GET /api/auditoria` and `/api/auditoria/verificar`.
- UTC dates in audit filters fixed (a mixed-timezone request no longer returns a 500) (#18).
- Login, failed login (one signed event per key and window, not per 429) and logout are audited; key-manager and upload actions made with a session are attributed to the real user (#39).

### Post-quantum cryptography and key manager
- Signed data manifest with **ML-DSA-65 (FIPS 204)** and an independent verification script (`scripts/pqc/verificar_manifiesto.py`).
- User e-mails encrypted at rest with **ML-KEM-1024 (FIPS 203) + AES-GCM** and searched by HMAC (#12).
- Per-user key manager (generate, revoke, rotate; the private key is delivered once and never stored) (#5); persistent development keys with Docker Compose (#30).

### Login (Argon2id)
- Migration `0005` adds `usuarios.password_hash` and `sesiones.token_hash` (#37).
- `POST /api/auth/login`, `/logout`, `GET /api/auth/me` (fingerprints of public keys only): Argon2id (OWASP profile, bounded concurrency), opaque 256-bit token in an `httpOnly`, `SameSite=Strict` `__session` cookie, rotation on login, per-e-mail and per-IP attempt limits reserved before checking the password, CSRF check, roles (upload: analyst, reviewer, admin; key manager: admin) (#39).
- Seed command `python -m app.cli.seed_usuarios` for three test users with their post-quantum keys; credentials go to a `600` file, never to the repository or the logs (#40).
- Security headers on every response (#38).

### Standardized messages
- Centralized error handlers and a common error body (`detail` unchanged plus a stable `error` object with code, title, message and action), `X-Request-ID`, structured logs (#19, #27).
- Texts for people, without developer jargon (#32) and 22 more domain codes for the most visible 409, 422 and 503 errors (#41).

### Layered architecture
- Key-manager schemas moved out of the router (#31); service, repository and mapper layers for the key manager (#34) and for CSV upload (#36), documented in `docs/arquitectura.md`. Unit tests use fake repositories.
- Replaced and not merged: #35 (superseded by #36); #9 (database row-level security with Supabase, discarded in favour of GCP).

### Deployment
- Docker image without root, with seed data, health check and `MALLOC_ARENA_MAX=2` to bound Argon2 memory; Cloud Run behind Firebase Hosting (only the `__session` cookie is forwarded) (#6, #39).
- Configurable CORS, credentials only for listed origins and a safe `CORS_ORIGIN_REGEX` (#4, #39); complete `.env.example` (#11).
- Documentation in English: README and sources report (#14, #15).

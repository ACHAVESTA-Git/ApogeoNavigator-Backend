# Backend architecture: layers and the pattern in use

The API follows one layering pattern. It is applied today to the **key manager** (`/api/admin/claves`) and to the **CSV
upload** (`/api/datos`). The other domains (audit, read endpoints, simulation) keep their current shape and are listed
under *Future work*.

```
router (app/routers)        HTTP only: parameters, status codes, response headers
  └─ service (app/services/<domain>)   business rules; no FastAPI, no queries
       ├─ repository (app/repositories)   all queries and the session lifecycle
       ├─ mapper (app/mappers)            entities -> DTOs (app/schemas)
       └─ collaborators                   crypto manager, auditor: injected, replaceable
app/api/deps.py             the only place that wires implementations together
```

## Rules

1. **Routers are thin.** They declare the route, call one service method and shape the HTTP response (for example the
   `no-store` headers and the background task of a key delivery). No `select`, no `commit`, no business decisions.
2. **A service is a `typing.Protocol` plus an `*ServiceImpl`.** Routers depend on the protocol
   (`ClavesService`), `app/api/deps.py` builds the implementation. Services raise `ErrorServicio(status, detail)`;
   `register_handlers` turns it into the standard error body, so the JSON the clients see does not change.
3. **Repositories own persistence.** `ClavesRepository` is a protocol and `SqlClavesRepository` the SQLAlchemy
   implementation (including row locks and the page query). Services write no queries and never import
   `sqlalchemy.select`. They are not fully persistence-agnostic: they still build ORM entities (`ClaveUsuario`) and
   catch `sqlalchemy.exc.IntegrityError` to turn the unique-index race into a 409. Decoupling that completely (domain
   objects plus a repository-level `Conflicto` error) is future work.
4. **Mappers are pure functions** from entities to DTOs. They do no I/O and no cryptography (the decrypted e-mail is
   passed in by the service).
5. **Collaborators are injected.** `ClavesServiceImpl(repo, auditor, gestor)` receives the audit writer and the crypto
   module, so the unit tests in `tests/test_servicio_claves.py` run with fakes: no database, no real keys.
6. **The HTTP contract stays frozen.** A refactor to layers must leave `tests/test_contrato_congelado.py` green without
   touching `tests/contrato/snapshots.json`.

## Where things live (key manager)

| Concern | File |
|---|---|
| Routes, auth dependency | `app/routers/admin_claves.py` |
| DTOs | `app/schemas/claves.py` |
| Service contract / implementation | `app/services/claves/servicio.py`, `impl.py` |
| Audit adapter | `app/services/claves/auditor.py` |
| Queries | `app/repositories/claves.py` |
| Entity -> DTO | `app/mappers/claves.py` |
| Wiring | `app/api/deps.py` |

## Where things live (CSV upload)

| Concern | File |
|---|---|
| Routes, the 422 report response | `app/routers/datos.py` |
| Service contract / implementation | `app/services/carga/servicio.py`, `impl.py` |
| Result -> DTO | `app/mappers/carga.py` |

`CargaServiceImpl.guardar_carga()` is the atomic save: it writes the audit event first, saves the file (keeping the
previous version in `_historial/`) and commits afterwards, so a signing or audit failure saves nothing. This domain has no
repository: it does not query the database; persistence goes through the audit and datastore collaborators, which are
injected (`auditoria`, `datos`, `almacen`). The `request` object is passed to the service only as an opaque `origen`
for the IP hash of the audit event.

## Where things live (login and sessions)

| Concern | File |
|---|---|
| Routes (`/api/auth/login`, `/logout`, `/me`) | `app/routers/auth.py` |
| DTOs (no private material) | `app/schemas/auth.py` |
| Service contract / implementation | `app/services/auth/servicio.py`, `impl.py`, `auditor.py` |
| Queries (users by e-mail, sessions by token hash) | `app/repositories/auth.py` |
| Argon2id | `app/security/passwords.py` |
| Authentication of protected routes (API key **or** session, roles, CSRF) | `app/api/deps.py` |

How it works:

* **Passwords** are hashed with Argon2id (`argon2-cffi`) with the OWASP profile (m=19 MiB, t=2, p=1). At most 2 verifications run at once
  (a semaphore); if none frees up within a second the answer is 503 with `Retry-After`, so a burst of logins cannot exhaust the
  memory of a small instance. A missing user is verified against a dummy hash so the response
  time does not reveal whether the e-mail exists; the answer is always the same 401.
* **Session** = an opaque random token (256 bits) in an `httpOnly`, `SameSite=Strict` cookie named `__session` (Firebase Hosting only
  forwards that cookie name to Cloud Run) (`Secure` in production) valid for
  8 hours. Only its SHA3-256 is stored (`sesiones.token_hash`). Login rotates the token (a presented cookie is revoked);
  logout revokes it.
* **Attempts** are limited per e-mail (5 / 15 min) and per IP (20 / 15 min) with the in-memory sliding-window limiter, then 429 with
  `Retry-After`. The attempt is **reserved before** the password is checked, so a concurrent burst cannot get past the limit; a correct
  login gives it back. Everything is in the signed audit chain (`LOGIN`, `LOGIN_FALLIDO`, `LOGOUT`); no e-mail or password is logged.
  When the limit trips, one event per key and window is signed (and at most 10 per minute overall), not one per 429.
* **Upload guard**: `app/core/limits.py` is a pure ASGI middleware. It authenticates for real (API key, or a live session looked up in the
  database; a made-up cookie does not skip it) and counts the bytes as they arrive, so a body without `Content-Length` is cut at the
  limit instead of being read whole into memory.
* **Audit actor**: key-manager and upload actions done with a session are audited with the real user (`actor=usuario`, `usuario_id`); with an
  API key they keep the key's actor.
* **Roles**: CSV upload accepts `analista`, `revisor` or `admin`; the key manager only `admin` (403 `ROL_INSUFICIENTE`).
  The `X-API-Key` of scripts keeps working unchanged.
* **CSRF**: besides `SameSite=Strict`, `POST /login` and any state-changing request authenticated by cookie must carry `X-Requested-With`
  (any value) or a trusted `Origin`; otherwise 403 `ORIGEN_NO_PERMITIDO`. CORS now allows credentials, only for the listed origins; `CORS_ORIGIN_REGEX` is rejected at startup if it does not compile or accepts an origin a third party can control (for example every `*.pages.dev`).
* The limiter is per process: with several Cloud Run instances each keeps its own counters (acceptable for the demo; a shared
  store is future work). Google OAuth is future work.

## Test-user seed

`python -m app.cli.seed_usuarios` (`app/cli/seed_usuarios.py`, a thin command) calls `app/services/semilla_usuarios.py`, which reuses
the key service (`ClavesServiceImpl.generar`) so the keys, the audit events and the one-time delivery follow exactly the same
path as the API. Passwords and private keys go only to a `600` file; the command is idempotent and refuses to run in production
without `--forzar-produccion`.

## Testing

* Business rules: `tests/test_servicio_claves.py` (fake repository, auditor and crypto manager) and
  `tests/test_servicio_carga.py` (fake audit, datastore and store).
* End to end over HTTP with a real SQLite database: `tests/test_gestor_claves.py`.
* Login, sessions, roles and CSRF: `tests/test_auth_login.py`. Test-user seed: `tests/test_semilla_usuarios.py`.
* Contract: `tests/test_contrato_congelado.py` (the three `/api/auth` operations were added to the snapshot on purpose; nothing existing changed).

## Future work (after the hackathon)

* Apply the same pattern to the audit endpoints (`routers/auditoria.py`, `utils/auditoria_util.py`, which is really a
  service with database access) and to the read and simulation endpoints (`services/lectura.py` is already close).
* Make the key service independent of the ORM (domain objects and a repository-raised conflict error).
* Unify the two ORM model locations (`app/db/models.py` and `app/models/`).

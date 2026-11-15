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

## Testing

* Business rules: `tests/test_servicio_claves.py` (fake repository, auditor and crypto manager) and
  `tests/test_servicio_carga.py` (fake audit, datastore and store).
* End to end over HTTP with a real SQLite database: `tests/test_gestor_claves.py`.
* Contract: `tests/test_contrato_congelado.py`.

## Future work (after the hackathon)

* Apply the same pattern to the audit endpoints (`routers/auditoria.py`, `utils/auditoria_util.py`, which is really a
  service with database access) and to the read and simulation endpoints (`services/lectura.py` is already close).
* Make the key service independent of the ORM (domain objects and a repository-raised conflict error).
* Unify the two ORM model locations (`app/db/models.py` and `app/models/`).

# Modelo de datos · Apogeo Navigator (Backend)

Base de datos: **PostgreSQL 16** (la misma versión mayor en local con Docker y en producción con Cloud SQL). Acceso con **SQLAlchemy 2.0** (modelos tipados con `Mapped[]`) y migraciones con **Alembic**. La conexión se configura solo con `DATABASE_URL`; sin ella la base de datos queda desactivada y la API de CSV sigue funcionando (sin auditoría).

Añadir una acción de auditoría, un rol o un tipo de carga exige una migración manual con `ALTER TYPE … ADD VALUE` (los enums son tipos nativos de PostgreSQL), además de cambiar `app/db/enums.py`; `tests/test_modelo_bd.py` comprueba que ambos coinciden.

Los CSV de `data/` **no** viven en la base de datos: la base guarda usuarios, sesiones y la auditoría de las cargas.

## Diagrama ER

```mermaid
erDiagram
    usuarios ||--o{ sesiones : "tiene"
    usuarios ||--o{ cargas_archivo : "sube"

    usuarios {
        uuid id PK
        bytea email_cifrado "ML-KEM-1024 + AES-GCM"
        varchar email_hash UK "HMAC-SHA256 del email normalizado"
        varchar nombre
        varchar proveedor_oauth
        varchar sub_oauth "UK con proveedor_oauth"
        rol_usuario rol "analista | revisor | admin"
        boolean activo
        timestamptz creado_utc
        timestamptz ultimo_login_utc
    }
    sesiones {
        uuid id PK
        uuid usuario_id FK
        timestamptz creada_utc
        timestamptz expira_utc
        boolean revocada
        varchar ip_hash "HMAC-SHA256, nunca la IP"
        varchar user_agent
    }
    cargas_archivo {
        uuid id PK
        uuid usuario_id FK "nullable"
        varchar actor "usuario o api-key"
        tipo_carga tipo "sites | efemerides | metricas"
        varchar nombre_archivo
        varchar sha3_256
        bigint tamano_bytes
        int filas
        boolean valido
        int total_errores
        varchar ruta_almacenamiento
        varchar ruta_historial
        timestamptz creado_utc
        bytea firma_pqc
        varchar algoritmo_firma
        varchar huella_clave
    }
    eventos_auditoria {
        bigserial id PK
        bigint secuencia UK "1, 2, 3..."
        timestamptz ts_utc
        uuid usuario_id "sin FK"
        varchar actor
        accion_auditoria accion
        varchar recurso
        jsonb detalle
        varchar ip_hash
        varchar user_agent
        varchar hash_prev UK
        varchar hash_actual UK
        bytea firma_pqc
        varchar algoritmo_firma
        varchar huella_clave
    }
```

## Tablas

| Tabla | Para qué sirve | Índices y restricciones |
|---|---|---|
| `usuarios` | Cuentas. El login OAuth aún no se implementa; el modelo ya está listo. El email va **cifrado** (`email_cifrado`) y se busca por `email_hash`. | `email_hash` único (CHECK de 64 caracteres); `(proveedor_oauth, sub_oauth)` único |
| `sesiones` | Sesiones emitidas tras el login. Se borran con el usuario. | `usuario_id`, `expira_utc` |
| `cargas_archivo` | Una fila por validación o carga de CSV, con su huella SHA3-256 y firma. Si se borra el usuario, `usuario_id` pasa a `NULL` y se conserva `actor`. | `creado_utc`, `sha3_256`, `usuario_id` |
| `eventos_auditoria` | Registro encadenado con manipulación detectable. Solo se inserta (trigger en PostgreSQL). Sin FK a usuarios. | `ts_utc`, `usuario_id`, `accion`; `secuencia`, `hash_prev` y `hash_actual` únicos; CHECK de firma y longitud de hashes |

Acciones de auditoría: `LOGIN`, `LOGIN_FALLIDO`, `LOGOUT`, `CARGA_VALIDADA`, `CARGA_GUARDADA`, `CARGA_RECHAZADA`, `CAMBIO_ROL`, `CAMBIO_DATOS`.

## Cadena de hashes y firma (manipulación detectable)
Cada evento enlaza con el anterior y lleva un número de secuencia (1, 2, 3…):

```
evento_canonico = JSON canónico (claves ordenadas, sin espacios, UTF-8) de
                  {secuencia, ts_utc, usuario_id, actor, accion, recurso, detalle, ip_hash, user_agent,
                   algoritmo_firma, huella_clave}
hash_actual     = SHA3-256( hash_prev + evento_canonico )
documento_firmado = {v, secuencia, hash_prev, hash_actual, ts_utc, usuario_id, accion, algoritmo_firma, huella_clave}
firma_pqc       = ML-DSA-65( JSON canónico de documento_firmado, contexto="auditoria" )   # app/security/pqc.py
```
- Una sola función (`canonical_event` en `app/utils/auditoria_util.py`) define el evento canónico y la usan tanto la inserción como la verificación. El JSON canónico es el de `pqc.canonicalizar`.
- El primer evento usa `hash_prev = "0" × 64` y `secuencia = 1`.
- **Qué entra en el hash:** la secuencia, el algoritmo y la huella de la clave forman parte del evento, así que un evento «NINGUNO» (sin firma) no se puede hacer pasar por uno firmado ni reordenarse con un eslabón ausente sin romper el hash.
- **Concurrencia:** en PostgreSQL cada escritura toma `pg_advisory_xact_lock` (constante `AUDIT_ADVISORY_LOCK_KEY`) antes de leer la cabeza de la cadena, incluso con la tabla vacía; `secuencia` y `hash_prev` son únicos como red de seguridad y la escritura se reintenta si colisionan. Probado con 16 hilos contra PostgreSQL 16 (`tests/test_postgres.py`).
- **Firma:** el CHECK `(firma_pqc IS NULL) = (algoritmo_firma = 'NINGUNO')` impide filas medio firmadas. En producción (`K_SERVICE` o `ENVIRONMENT=production`) o con `AUDIT_REQUIRE_SIGNATURE=1`, si no se puede firmar el evento **no se escribe** y la API responde 503; lo mismo si no hay base de datos configurada. En desarrollo se guarda con algoritmo `NINGUNO` y `verificar_cadena()` lo da por **no válido**.

### Qué detecta y qué no
- `verificar_cadena()` (y `GET /api/auditoria/verificar`) comprueba génesis, secuencia consecutiva, enlaces, hashes recalculados, algoritmo, huella de la clave y firma de cada evento. Devuelve también `n_eventos`, `hash_cabeza`, `ultima_secuencia` y `primer_hash_prev_es_genesis`.
- **Detecta** cambiar, borrar o reordenar un evento intermedio y rehacer hashes sin la clave privada (no se puede firmar).
- **No detecta, por sí sola:** borrar los últimos N eventos, ni vaciar la tabla y empezar de cero desde el génesis (el génesis es público). Para eso hay un **ancla**: cada escritura confirmada emite una línea de log `auditoria cabeza secuencia=N hash=…` (solo tras el *commit*, nunca si hay rollback). Compara `hash_cabeza` y `ultima_secuencia` con la última línea del log. El ancla vive solo en logs, con la retención limitada de Cloud Logging; si hace falta más garantía, publica periódicamente la cabeza firmada en un objeto de Cloud Storage (trabajo futuro). No hay un evento génesis firmado explícito (trabajo futuro).
- **Inmutabilidad en PostgreSQL:** la migración crea un trigger que lanza una excepción en `UPDATE`, `DELETE` y `TRUNCATE` de `eventos_auditoria`. El propietario de la tabla podría quitar el trigger, por eso la aplicación **no** debe ser propietaria (ver «Usuarios con mínimo privilegio»). La cadena sigue detectando lo que el trigger no impida.
- `eventos_auditoria.usuario_id` **no tiene clave foránea**: borrar un usuario no debe tocar la auditoría. Los usuarios se desactivan (`activo = false`) en lugar de borrarse.
- `detalle` solo admite `str`, `int`, `bool`, `None`, listas y dicts con claves de texto (sin floats ni fechas) y hasta 4 KB; `actor`, `recurso`, `nombre_archivo` y `user_agent` se limpian de caracteres de control y se recortan.

## Privacidad
- **El email de los usuarios se guarda cifrado en reposo** (Ley 29733): `email_cifrado` (ML-KEM-1024 con AES-GCM, clave KEM del servidor) y `email_hash` (HMAC-SHA256 del email normalizado, para búsquedas exactas sin descifrar). No hay búsqueda parcial por email. Todo acceso pasa por `app/services/usuarios.py` (`crear_usuario`, `buscar_por_email`, `email_de`). Las claves son `PQC_KEM_PRIVATE_KEY` y `EMAIL_HASH_KEY` (obligatorias en producción, en el gestor de secretos). **Si se pierden la clave KEM y sus anteriores, los emails son irrecuperables:** guarda versiones del secreto.
- **Rotación:** despliega la clave nueva en `PQC_KEM_PRIVATE_KEY` / `EMAIL_HASH_KEY` y la vieja en `PQC_KEM_PRIVATE_KEY_ANTERIORES` / `EMAIL_HASH_KEY_ANTERIORES`; ejecuta `rotar_cifrado(session)` (vuelve a cifrar y recalcula el hash; es idempotente); cuando devuelva 0 y 0, retira las anteriores.
- **Migraciones 0003 y 0004:** la 0003 añade las columnas, cifra los emails existentes y comprueba fila a fila que descifrar devuelve el original; la 0004 vuelve a comprobarlo y solo entonces borra `email`. Si algo no coincide aborta sin tocar nada. Con filas existentes hacen falta las claves del servidor al migrar; sin filas no. El downgrade restituye `email` descifrando.
- **Nunca se guarda la IP en claro.** `ip_hash = HMAC-SHA256(IP_HASH_SALT, ip)` en hexadecimal. Sin `IP_HASH_SALT` no se guarda ningún hash de IP (queda `NULL`). En producción, con base de datos, el arranque **falla** si falta la sal.
- Es **seudonimización**, no anonimización: con una sal fija, un hash de IPv4 se puede invertir probando los 2^32 valores si alguien obtiene la sal. Guarda la sal en Secret Manager y trata `ip_hash` como dato personal.
- Detrás de Firebase Hosting y Cloud Run la IP del cliente llega en `X-Forwarded-For`; se toma el primer valor. Es un dato informativo (puede falsearse), no una prueba de identidad. Los logs no contienen la IP.
- `user_agent` se recorta a 200 caracteres. `detalle` no debe contener datos personales ni contenido de los CSV.
- **Retención (trabajo futuro):** `ip_hash` y `user_agent` entran en una cadena inmutable y no se pueden borrar; hace falta definir un plazo y una política (p. ej. dejar de guardarlos pasado un tiempo) antes de abrir el servicio al público. Tampoco hay aún limpieza de sesiones expiradas.

## Migraciones (Alembic)
```bash
# local (Docker): corren solas al arrancar el servicio backend de docker-compose.yml
docker compose up --build

# a mano
DATABASE_URL=postgresql+psycopg://apogeo:apogeo_dev@localhost:5432/apogeo alembic upgrade head
alembic revision --autogenerate -m "descripción"     # tras cambiar app/db/models.py
```
`tests/test_modelo_bd.py` comprueba que la migración y los modelos producen el mismo esquema. Las migraciones se ejecutan como **paso previo al despliegue**, no al arrancar la instancia.

## Variables de entorno
| Variable | Uso |
|---|---|
| `DATABASE_URL` | URL de SQLAlchemy. Vacía = base de datos desactivada. |
| `IP_HASH_SALT` | Sal del HMAC de la IP. |
| `POSTGRES_PASSWORD` | Solo para `docker-compose.yml` (por defecto `apogeo_dev`, solo desarrollo). |

Formatos de `DATABASE_URL`:
- Local (Docker): `postgresql+psycopg://apogeo:apogeo_dev@postgres:5432/apogeo`
- Cloud Run + Cloud SQL (socket Unix): `postgresql+psycopg://APP_USER:CLAVE@/apogeo?host=/cloudsql/PROYECTO:REGION:INSTANCIA` (si la clave tiene caracteres especiales, codifícala como URL). Por **IP pública** (no recomendado) exige TLS añadiendo `?sslmode=verify-full&sslrootcert=…`; por el socket Unix del proxy el tráfico ya va cifrado.

## Producción en Cloud SQL
Decisión del usuario: PostgreSQL 16 en **Cloud SQL para PostgreSQL**, pagado con el crédito del proyecto «Apogeo». Esta sección es referencia: **no se ha creado ningún recurso**. Los comandos crean recursos con coste: ejecútalos solo con confirmación del usuario y con `PROYECTO`, `REGION` e `INSTANCIA` sustituidos.

### Conexión desde Cloud Run (la opción más simple)
Se usa el **socket Unix** que Cloud Run monta en `/cloudsql/…`; no hace falta ninguna librería extra (psycopg ya lo entiende con `host=/cloudsql/…`). El Cloud SQL Python Connector añadiría una dependencia y no aporta nada a este proyecto.
```bash
gcloud services enable sqladmin.googleapis.com secretmanager.googleapis.com
gcloud run deploy apogeo-backend ... \
  --add-cloudsql-instances PROYECTO:REGION:INSTANCIA \
  --set-secrets DATABASE_URL=apogeo-database-url:latest,IP_HASH_SALT=apogeo-ip-hash-salt:latest,UPLOAD_API_KEY=apogeo-upload-api-key:latest
```
La cuenta de servicio de Cloud Run necesita `roles/cloudsql.client` y `roles/secretmanager.secretAccessor` (solo sobre esos secretos).

### Instancia mínima para cuidar el crédito
- Versión: **PostgreSQL 16**. Edición **Enterprise** (la más pequeña; Enterprise Plus es para cargas mucho mayores).
- Tipo de máquina: el tamaño compartido más pequeño de la edición Enterprise (`db-f1-micro` o `db-g1-small`). Son nodos de núcleo compartido, sin SLA, aptos para una demo.
- Almacenamiento: SSD de 10 GB (el mínimo) sin crecimiento automático ilimitado (`--storage-auto-increase-limit`). Un solo nodo, **sin alta disponibilidad** (HA duplica el coste de la instancia) y sin réplicas de lectura.
- Región: la misma que Cloud Run, para evitar tráfico entre regiones.
- Ahorro: fuera de la demo, detén la instancia (`gcloud sql instances patch INSTANCIA --activation-policy=NEVER`); mientras está detenida se sigue pagando el almacenamiento. Bórrala al terminar el hackathon.
- Precios: **no se citan cifras aquí.** Consúltalos en https://cloud.google.com/sql/pricing y en la calculadora https://cloud.google.com/products/calculator, y confirma que el crédito cubre el gasto previsto antes de crear la instancia. Añade una alerta de presupuesto (ver `docs/manuales/firebase-gcp.md`, sección 7).

```bash
gcloud sql instances create INSTANCIA --database-version=POSTGRES_16 --edition=ENTERPRISE \
  --tier=db-g1-small --region=REGION --storage-size=10GB --storage-type=SSD \
  --backup-start-time=03:00 --availability-type=zonal
gcloud sql databases create apogeo --instance=INSTANCIA
```
(Comprueba los nombres de las opciones con `gcloud sql instances create --help`: pueden cambiar entre versiones del CLI.)

### Backups
- Activa los **backups automáticos diarios** (`--backup-start-time`) y la **recuperación a un momento dado** (PITR) si el coste lo permite.
- Antes de cada migración o cambio importante: `gcloud sql backups create --instance=INSTANCIA`.
- Prueba una restauración al menos una vez en una instancia aparte antes de la entrega.
- La cadena de auditoría se verifica después de restaurar (`verificar_cadena()`).

### Usuarios con mínimo privilegio
No uses el usuario `postgres` desde la aplicación. Crea dos roles:
```sql
-- Dueño del esquema: solo para Alembic (paso previo al despliegue).
CREATE ROLE apogeo_migrator LOGIN PASSWORD '...';
-- Aplicación: sin DDL.
CREATE ROLE apogeo_app LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE apogeo TO apogeo_app;
GRANT USAGE ON SCHEMA public TO apogeo_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON usuarios, sesiones, cargas_archivo TO apogeo_app;
GRANT SELECT, INSERT ON eventos_auditoria TO apogeo_app;      -- la auditoría solo se añade
REVOKE UPDATE, DELETE, TRUNCATE ON eventos_auditoria FROM apogeo_app;
GRANT USAGE, SELECT ON SEQUENCE eventos_auditoria_id_seq TO apogeo_app;
```
`apogeo_app` **no** debe ser propietaria de las tablas (el propietario puede quitar el trigger y vaciar la auditoría); las crea `apogeo_migrator`. Los permisos se aplican después de la primera migración (y tras cada migración que cree tablas). Las migraciones usan `apogeo_migrator`; la aplicación usa `apogeo_app`. Cuando exista OAuth, valora la autenticación IAM de Cloud SQL para no manejar contraseñas.

### Secretos (Secret Manager)
`DATABASE_URL`, `IP_HASH_SALT`, `UPLOAD_API_KEY` y las claves PQC (`PQC_PRIVATE_KEY`, `PQC_PUBLIC_KEY`) van en **Secret Manager** y se inyectan con `--set-secrets`; nunca en el repositorio, en la imagen ni en `--set-env-vars`.
```bash
printf '%s' 'postgresql+psycopg://apogeo_app:CLAVE@/apogeo?host=/cloudsql/PROYECTO:REGION:INSTANCIA' \
  | gcloud secrets create apogeo-database-url --data-file=-
```

### Migraciones como paso previo al despliegue
Con la misma imagen del backend, como trabajo de Cloud Run (o desde una máquina con Cloud SQL Auth Proxy), usando el rol `apogeo_migrator`:
```bash
gcloud run jobs create apogeo-migrar --image REGION-docker.pkg.dev/PROYECTO/apogeo/backend:v1 \
  --region REGION --set-cloudsql-instances PROYECTO:REGION:INSTANCIA \
  --set-secrets DATABASE_URL=apogeo-database-url-migrator:latest \
  --command alembic --args upgrade,head
gcloud run jobs execute apogeo-migrar --region REGION --wait
```
Solo después se despliega la nueva revisión del servicio.

### Pool de conexiones
Cloud Run puede levantar muchas instancias y Cloud SQL limita las conexiones simultáneas según el tamaño de la máquina. Por eso el pool es pequeño: `pool_size=2`, `max_overflow=2`, `pool_pre_ping=True` y reciclado a los 30 min (constantes `DB_POOL_*` en `app/core/config.py`). Limita también `--max-instances` en Cloud Run (el manual usa 3).

## Pruebas
`pytest` usa SQLite (en memoria o en un archivo temporal): es lo más simple y no necesita Docker. Los tipos son portables (`JSONB` y `BIGSERIAL` en PostgreSQL, `JSON` e `INTEGER` en SQLite). `tests/test_postgres.py` (omitido sin `TEST_DATABASE_URL`) ejecuta contra PostgreSQL 16 real el trigger, los CHECK, los tipos, la concurrencia con bloqueo consultivo y la subida y bajada de la migración; se ejecutó con éxito con el servicio `postgres` de `docker-compose.yml`.

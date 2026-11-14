# Modelo de datos · Apogeo Navigator (Backend)

Base de datos: **PostgreSQL 16** (la misma versión mayor en local con Docker y en producción con Cloud SQL). Acceso con **SQLAlchemy 2.0** (modelos tipados con `Mapped[]`) y migraciones con **Alembic**. La conexión se configura solo con `DATABASE_URL`; sin ella la base de datos queda desactivada y la API de CSV sigue funcionando (sin auditoría).

Los CSV de `data/` **no** viven en la base de datos: la base guarda usuarios, sesiones y la auditoría de las cargas.

## Diagrama ER

```mermaid
erDiagram
    usuarios ||--o{ sesiones : "tiene"
    usuarios ||--o{ cargas_archivo : "sube"
    usuarios ||--o{ eventos_auditoria : "origina"

    usuarios {
        uuid id PK
        varchar email UK
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
    }
    eventos_auditoria {
        bigserial id PK
        timestamptz ts_utc
        uuid usuario_id FK "nullable"
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
    }
```

## Tablas

| Tabla | Para qué sirve | Índices y restricciones |
|---|---|---|
| `usuarios` | Cuentas. El login OAuth aún no se implementa; el modelo ya está listo. | `email` único; `(proveedor_oauth, sub_oauth)` único |
| `sesiones` | Sesiones emitidas tras el login. Se borran con el usuario. | `usuario_id`, `expira_utc` |
| `cargas_archivo` | Una fila por validación o carga de CSV, con su huella SHA3-256 y firma. Si se borra el usuario, `usuario_id` pasa a `NULL` y se conserva `actor`. | `creado_utc`, `sha3_256`, `usuario_id` |
| `eventos_auditoria` | Registro encadenado e infalsificable de acciones. Solo se inserta. | `ts_utc`, `usuario_id`, `accion`; `hash_prev` y `hash_actual` únicos |

Acciones de auditoría: `LOGIN`, `LOGIN_FALLIDO`, `LOGOUT`, `CARGA_VALIDADA`, `CARGA_GUARDADA`, `CARGA_RECHAZADA`, `CAMBIO_ROL`, `CAMBIO_DATOS`.

## Cadena de hashes y firma
Cada evento enlaza con el anterior:

```
evento_canonico = JSON ordenado y compacto de {ts_utc, usuario_id, actor, accion, recurso, detalle, ip_hash, user_agent}
hash_actual     = SHA3-256( hash_prev + evento_canonico )
firma_pqc       = ML-DSA-65( hash_actual, contexto="auditoria" )      # app/security/pqc.py
```
- El primer evento usa `hash_prev = "0" × 64`.
- `hash_prev` es único: dos eventos no pueden colgar del mismo anterior, así que la cadena no se bifurca. Ante una colisión concurrente, el registro se reintenta.
- Alterar, borrar o reordenar un evento rompe `hash_actual` del siguiente. `verificar_cadena()` lo detecta recalculando los hashes y comprobando las firmas.
- La cadena **detecta** la manipulación; no la impide. Quien tenga acceso de escritura a la base puede recalcular los hashes, pero no firmar sin la clave privada PQC (que no está en la base).
- Los eventos no se actualizan ni se borran. El usuario de la aplicación en producción solo debe tener `INSERT` y `SELECT` sobre `eventos_auditoria`.

## Privacidad
- **Nunca se guarda la IP en claro.** `ip_hash = HMAC-SHA256(IP_HASH_SALT, ip)` en hexadecimal. Sin `IP_HASH_SALT` no se guarda ningún hash de IP (queda `NULL`).
- La sal vive en una variable de entorno (en producción, en Secret Manager) y no se versiona. Cambiar la sal impide correlacionar con hashes anteriores.
- `user_agent` se recorta a 200 caracteres. `detalle` no debe contener datos personales ni contenido de los CSV.
- Detrás de Firebase Hosting y Cloud Run la IP del cliente llega en `X-Forwarded-For`; se toma el primer valor. Es un dato informativo (puede falsearse), no una prueba de identidad.

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
- Cloud Run + Cloud SQL (socket Unix): `postgresql+psycopg://APP_USER:CLAVE@/apogeo?host=/cloudsql/PROYECTO:REGION:INSTANCIA` (si la clave tiene caracteres especiales, codifícala como URL).

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
GRANT USAGE, SELECT ON SEQUENCE eventos_auditoria_id_seq TO apogeo_app;
```
Los permisos se aplican después de la primera migración (y tras cada migración que cree tablas). Las migraciones usan `apogeo_migrator`; la aplicación usa `apogeo_app`. Cuando exista OAuth, valora la autenticación IAM de Cloud SQL para no manejar contraseñas.

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
`pytest` usa SQLite (en memoria o en un archivo temporal): es lo más simple y no necesita Docker. Los tipos son portables (`JSONB` y `BIGSERIAL` en PostgreSQL, `JSON` e `INTEGER` en SQLite). La migración se ha comprobado además contra PostgreSQL 16 real con `docker compose up`.

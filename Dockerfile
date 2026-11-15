FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Usuario sin privilegios: el contenedor no corre como root.
RUN useradd --system --uid 10001 --user-group --no-create-home app

COPY app ./app
# Migraciones: se ejecutan como paso previo al despliegue (`alembic upgrade head`), no al arrancar.
COPY alembic.ini ./
COPY alembic ./alembic
# Semilla de datos: sirve para que la imagen funcione sin volumen. Con DATA_BUCKET el bucket manda y la semilla solo
# se copia si el bucket está vacío; sin bucket (solo desarrollo) se copia a DATA_DIR si está vacío.
COPY data ./seed-data

# DATA_DIR (réplica local, efímera en Cloud Run) y la carpeta de claves de desarrollo deben ser escribibles.
RUN mkdir -p /srv/data /srv/.pqc_dev /srv/.seed && chown -R app:app /srv/data /srv/.pqc_dev /srv/.seed

# Cloud Run inyecta $PORT; 8080 por defecto en local.
ENV PORT=8080 DATA_DIR=/srv/data DATA_SEED_DIR=/srv/seed-data PQC_DEV_KEY_DIR=/srv/.pqc_dev SEED_CREDENTIALS_FILE=/srv/.seed/credenciales.local
EXPOSE 8080
USER app

HEALTHCHECK --interval=30s --timeout=4s --start-period=25s --retries=3 \
  CMD ["python", "-c", "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/api/health', timeout=3).status == 200 else 1)"]

# exec: uvicorn pasa a ser el proceso principal y recibe SIGTERM (cierre ordenado en Cloud Run).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

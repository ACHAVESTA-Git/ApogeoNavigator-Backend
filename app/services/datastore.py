"""Datos en Cloud Storage con réplica local.

Las lecturas usan siempre la carpeta local (DATA_DIR): rápida y compatible con el resto del código. Con DATA_BUCKET
el bucket es la fuente de verdad:
  - al arrancar se descarga todo; si el bucket está vacío se siembra con la semilla de la imagen;
  - cada cierto tiempo (DATA_REFRESH_SECONDS) se comprueba si cambió y se vuelve a descargar lo modificado;
  - cada carga válida se guarda en local (con su manifiesto firmado) y se publica en el bucket con precondición de
    generación: si otro proceso cambió el mismo objeto, no se pisa y se responde 409.
Sin bucket (solo desarrollo) todo ocurre en el disco local. En producción sin bucket el arranque falla.

Límite conocido: con varias instancias escribiendo a la vez puede quedar una ventana entre subir el CSV y subir el
manifiesto en la que otro proceso vea ambos desfasados. Para la demo, fija --max-instances=1 en el servicio.
"""
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

from app.core import config as c
from app.security import pqc
from app.services import storage
from app.services.blobstore import BlobStore, GcsStore, StoreConflict, StoreError

log = logging.getLogger(c.LOGGER_DATASTORE)


class DataConflict(Exception):
    """Otro proceso modificó los datos mientras se guardaba."""


_lock = threading.RLock()
_store: BlobStore | None = None
_store_resolved = False
_generations: dict[str, int] = {}
_last_check = float("-inf")


# ------------------------------------------------------------------ almacén
def set_store(store: BlobStore | None) -> None:
    """Inyecta el almacén (pruebas) y reinicia el estado de sincronización."""
    global _store, _store_resolved, _last_check
    with _lock:
        _store, _store_resolved, _last_check = store, True, float("-inf")
        _generations.clear()


def reset() -> None:
    """Vuelve a resolver el almacén desde las variables de entorno."""
    global _store, _store_resolved, _last_check
    with _lock:
        _store, _store_resolved, _last_check = None, False, float("-inf")
        _generations.clear()


def get_store() -> BlobStore | None:
    global _store, _store_resolved
    if not _store_resolved:
        bucket = c.get_data_bucket()
        _store = GcsStore(bucket, c.get_data_bucket_prefix()) if bucket else None
        _store_resolved = True
    return _store


# ------------------------------------------------------------------ utilidades de disco
def _write_local(name: str, data: bytes) -> None:
    destination = c.get_data_dir() / name
    folder = destination.parent
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=c.TMP_PREFIX, suffix=c.TMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, c.FILE_MODE)
        os.replace(tmp, destination)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _is_data_object(name: str) -> bool:
    if c.BUCKET_PATH_SEP in name:
        return bool(c.PROYECCION_OBJECT_RE.fullmatch(name))      # data/proyecciones/: fuera del manifiesto firmado
    return name.endswith(c.SEED_SUFFIX) or name == c.MANIFEST_NAME


def _seed_files() -> dict[str, bytes]:
    seed = c.get_seed_dir()
    if seed is None or not seed.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in sorted(seed.glob(f"*{c.SEED_SUFFIX}")) if p.is_file()}


# ------------------------------------------------------------------ sincronización
def pull(force: bool = False) -> None:
    """Descarga lo que cambió en el bucket y borra de local lo que ya no está (el bucket manda)."""
    store = get_store()
    if store is None:
        return
    with _lock:
        remote = {b.name: b.generation for b in store.list() if _is_data_object(b.name)}
        folder = c.get_data_dir()
        for name, generation in remote.items():
            if force or _generations.get(name) != generation or not (folder / name).is_file():
                _write_local(name, store.download(name))
                _generations[name] = generation
        for name in set(_generations) - set(remote):
            (folder / name).unlink(missing_ok=True)
            _generations.pop(name, None)
        if folder.is_dir():
            for path in (*folder.glob(f"*{c.SEED_SUFFIX}"), folder / c.MANIFEST_NAME):   # incluye un manifiesto local huérfano
                if path.name not in remote:
                    path.unlink(missing_ok=True)
        global _last_check
        _last_check = time.monotonic()


def refresh_if_stale() -> None:
    """Dependencia de las lecturas: como mucho una consulta al bucket cada DATA_REFRESH_SECONDS."""
    if get_store() is None:
        return
    if time.monotonic() - _last_check < c.get_data_refresh_seconds():
        return
    try:
        pull()
    except StoreError:
        log.warning(c.MSG_BUCKET_UNAVAILABLE)    # se sirven los datos locales que ya había


def startup() -> None:
    """Arranque: exige bucket en producción, siembra si hace falta y deja DATA_DIR listo."""
    if c.is_production() and not c.get_data_bucket():
        raise RuntimeError(c.MSG_PROD_NEEDS_BUCKET)
    store = get_store()
    if store is None:
        _seed_local_if_empty()
        _manifest_local_if_missing()
        return
    with _lock:
        if not [b for b in store.list() if _is_data_object(b.name)]:
            _seed_bucket(store)
        pull(force=True)


def _seed_local_if_empty() -> None:
    folder = c.get_data_dir()
    if folder.is_dir() and any(folder.glob(f"*{c.SEED_SUFFIX}")):
        return
    for name, data in _seed_files().items():
        _write_local(name, data)


def _manifest_local_if_missing() -> None:
    """Desarrollo local sin bucket: si falta manifest.json lo firma con las claves de desarrollo. Nunca en producción."""
    folder = c.get_data_dir()
    if c.is_production() or not folder.is_dir() or (folder / c.MANIFEST_NAME).exists():
        return
    try:
        pqc.escribir_manifiesto(folder, pqc.generar_manifiesto(folder))
    except pqc.ErrorClavesPQC as exc:
        log.warning(c.MSG_LOCAL_MANIFEST_SKIPPED, type(exc).__name__)


def _seed_bucket(store: BlobStore) -> None:
    """Bucket vacío: sube la semilla y, si se puede, su manifiesto firmado."""
    seed = _seed_files()
    try:
        for name, data in seed.items():
            store.upload(name, data, if_generation_match=c.NEW_OBJECT_GENERATION)
    except StoreConflict:
        return                                              # otra instancia arrancó a la vez y ya sembró: sus datos mandan
    if not seed:
        return
    try:
        manifest = pqc.generar_manifiesto(c.get_seed_dir())
    except pqc.ErrorClavesPQC as exc:
        if c.is_production():
            raise                                           # producción: sin firma no se publican datos
        log.warning(c.MSG_SEED_MANIFEST_SKIPPED, type(exc).__name__)
        return
    try:
        store.upload(c.MANIFEST_NAME, _manifest_bytes(manifest), if_generation_match=c.NEW_OBJECT_GENERATION)
    except StoreConflict:
        return


def _manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# ------------------------------------------------------------------ guardado
def guardar(filename: str, content: bytes) -> Path | None:
    """Guarda un CSV validado. Con bucket: local (con manifiesto firmado) y luego bucket con precondiciones."""
    store = get_store()
    if store is None:
        return storage.save_atomic(filename, content)
    with _lock:
        try:
            pull(force=False)                                   # partir de la última versión del bucket
            backup = storage.save_atomic(filename, content)
            folder = c.get_data_dir()
            if backup is not None:
                store.upload(f"{c.BUCKET_HISTORY_PREFIX}{backup.name}", backup.read_bytes(),
                             if_generation_match=c.NEW_OBJECT_GENERATION)
            for name in (filename, c.MANIFEST_NAME):
                _generations[name] = store.upload(name, (folder / name).read_bytes(),
                                                  if_generation_match=_generations.get(name, c.NEW_OBJECT_GENERATION))
            return backup
        except StoreConflict as exc:
            _reset_replica()                                    # descartar lo local: manda el bucket
            raise DataConflict(c.MSG_DATA_CONFLICT) from exc
        except StoreError:
            _reset_replica()                                    # la subida falló tras escribir en local: deshacerlo
            raise


def _reset_replica() -> None:
    """Vuelve a descargar todo del bucket para que la réplica local no sirva datos que el bucket no tiene.

    Mejor esfuerzo: si el bucket sigue caído se anota, se mantiene el error original y se olvidan las generaciones
    conocidas para que refresh_if_stale() vuelva a descargarlo todo en cuanto el bucket responda. Límite: si la subida falla justo
    entre el CSV y el manifiesto, el bucket puede quedar con un CSV más nuevo que su manifiesto (el verificador del
    manifiesto lo detecta); repetir la carga lo corrige.
    """
    global _last_check
    try:
        pull(force=True)
    except StoreError as exc:
        log.error(c.MSG_REPLICA_RESET_FAILED, type(exc).__name__)
        with _lock:                       # con el bucket caído la réplica sigue sucia: que el próximo refresh la rehaga entera
            _generations.clear()
            _last_check = float("-inf")

"""Lectura de sites.csv y guardado atómico de CSV con copia en el historial."""
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core import config
from app.security import pqc
from app.validation.coherence import Context
from app.validation.engine import ValidationResult, validate_csv

_write_lock = threading.Lock()


def read_sites() -> ValidationResult | None:
    """Valida y devuelve sites.csv del disco; None si no existe."""
    path = config.get_data_dir() / config.SITES_FILENAME
    if not path.is_file():
        return None
    return validate_csv(config.TIPO_SITES, path.name, path.read_bytes())


def known_site_ids() -> set[str] | None:
    result = read_sites()
    if result is None:
        return None
    return {r.values["site_id"] for r in result.records}


def build_context() -> Context:
    return Context(known_site_ids=known_site_ids())


def save_atomic(filename: str, content: bytes) -> Path | None:
    """Guarda `content` como `filename` y regenera el manifiesto firmado. Devuelve la copia previa, si había.

    Orden dentro de la sección crítica: 1) firmar el manifiesto con el contenido nuevo (si falla, p. ej.
    ErrorClavesPQC, no se escribe nada); 2) copia al historial; 3) os.replace del CSV; 4) escribir manifest.json.
    Entre 3 y 4 hay una ventana mínima en la que el manifiesto vigente aún describe la versión anterior del CSV:
    quien verifique en ese instante verá una discrepancia transitoria.
    """
    data_dir = config.get_data_dir()
    target = data_dir / filename
    with _write_lock:
        data_dir.mkdir(parents=True, exist_ok=True)
        manifest = pqc.generar_manifiesto(data_dir, contenidos_nuevos={filename: content})
        backup = None
        if target.is_file():
            history = data_dir / config.HISTORY_DIRNAME
            history.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime(config.HISTORY_STAMP)
            backup = history / f"{target.stem}_{stamp}{target.suffix}"
            counter = 0
            while backup.exists():          # nunca pisar una copia previa
                counter += 1
                backup = history / f"{target.stem}_{stamp}_{counter}{target.suffix}"
            backup.write_bytes(target.read_bytes())
        fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=config.TMP_PREFIX, suffix=config.TMP_SUFFIX)
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.chmod(tmp_name, config.FILE_MODE)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        pqc.escribir_manifiesto(data_dir, manifest)
    return backup

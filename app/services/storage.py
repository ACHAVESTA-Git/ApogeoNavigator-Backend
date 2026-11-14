"""Lectura de sites.csv y guardado atómico de CSV con copia en el historial."""
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core import config
from app.validation.coherence import Context
from app.validation.engine import ValidationResult, validate_csv

_write_lock = threading.Lock()
HISTORY_STAMP = "%Y%m%dT%H%M%SZ"


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
    """Escribe a un temporal y renombra. Devuelve la ruta de la copia previa, si había."""
    data_dir = config.get_data_dir()
    target = data_dir / filename
    with _write_lock:
        data_dir.mkdir(parents=True, exist_ok=True)
        backup = None
        if target.is_file():
            history = data_dir / config.HISTORY_DIRNAME
            history.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime(HISTORY_STAMP)
            backup = history / f"{target.stem}_{stamp}{target.suffix}"
            backup.write_bytes(target.read_bytes())
        fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=".tmp_", suffix=".csv")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    return backup

"""Almacén de objetos para los datos: interfaz mínima y su implementación con Google Cloud Storage.

La autenticación usa las credenciales por defecto del entorno (cuenta de servicio de Cloud Run): no hay claves en el código.
"""
from typing import NamedTuple, Protocol

from app.core import config


class BlobInfo(NamedTuple):
    name: str
    generation: int


class StoreConflict(Exception):
    """El objeto cambió desde que se leyó (precondición de generación fallida)."""


class StoreError(Exception):
    """El almacén no responde o rechaza la operación."""


class BlobStore(Protocol):
    def list(self) -> list[BlobInfo]: ...

    def download(self, name: str) -> bytes: ...

    def upload(self, name: str, data: bytes, *, if_generation_match: int | None = None) -> int: ...


class GcsStore:
    """Cloud Storage. Los nombres se guardan bajo `prefix` (p. ej. «datos/»)."""

    def __init__(self, bucket_name: str, prefix: str = "", client=None):
        if client is None:
            from google.cloud import storage    # importación perezosa: solo si hay bucket
            client = storage.Client()
        self._bucket = client.bucket(bucket_name)
        self._client = client
        self._prefix = prefix

    def _guard(self, operation):
        from google.api_core import exceptions
        try:
            return operation()
        except exceptions.PreconditionFailed as exc:
            raise StoreConflict(str(exc)) from exc
        except (exceptions.GoogleAPICallError, exceptions.RetryError) as exc:
            raise StoreError(type(exc).__name__) from exc

    def list(self) -> list[BlobInfo]:
        blobs = self._guard(lambda: list(self._client.list_blobs(self._bucket, prefix=self._prefix)))
        return [BlobInfo(b.name.removeprefix(self._prefix), b.generation) for b in blobs if not b.name.endswith(config.BUCKET_PATH_SEP)]

    def download(self, name: str) -> bytes:
        return self._guard(lambda: self._bucket.blob(self._prefix + name).download_as_bytes())

    def upload(self, name: str, data: bytes, *, if_generation_match: int | None = None) -> int:
        blob = self._bucket.blob(self._prefix + name)
        self._guard(lambda: blob.upload_from_string(data, if_generation_match=if_generation_match))
        return blob.generation

"""Lector CSV seguro: tipo de archivo, tamaño, UTF-8/BOM, filas y cabecera."""
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from itertools import islice
from typing import BinaryIO, Iterator, Sequence

from app.core import config
from app.utils.errors import FileError

Row = tuple[int, list[str]]   # (fila lógica: cabecera = 1, primer dato = 2; campos)


@dataclass(frozen=True)
class HeaderIssue:
    column: str | None
    code: str
    message: str
    value: str | None = None


def read_limited(stream: BinaryIO) -> bytes:
    """Lee como mucho MAX_FILE_BYTES + 1 bytes para detectar archivos demasiado grandes."""
    limit = config.MAX_FILE_BYTES + 1
    chunks, total = [], 0
    while total < limit:
        chunk = stream.read(min(config.READ_CHUNK_BYTES, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def check_filename(filename: str | None, pattern: re.Pattern) -> re.Match:
    match = pattern.fullmatch(filename or "")
    if not match:
        raise FileError(config.E_NOMBRE_ARCHIVO, config.MSG_BAD_NAME.format(pattern=pattern.pattern))
    return match


def check_raw(filename: str | None, raw: bytes) -> None:
    """Extensión, tamaño, binario/codificación y vacío (en este orden)."""
    if not (filename or "").lower().endswith(config.CSV_EXTENSION):
        raise FileError(config.E_TIPO_ARCHIVO, config.MSG_BAD_EXTENSION.format(ext=config.CSV_EXTENSION))
    if len(raw) > config.MAX_FILE_BYTES:
        raise FileError(config.E_TAMANO, config.MSG_TOO_BIG.format(mb=config.MAX_FILE_MB))
    if raw.startswith(config.UTF16_BOMS):
        raise FileError(config.E_CODIFICACION, config.MSG_ENCODING)
    if config.NUL_BYTE in raw or raw.startswith(config.BINARY_MAGIC_PREFIXES):
        raise FileError(config.E_TIPO_ARCHIVO, config.MSG_BINARY)
    if not raw.replace(config.UTF8_BOM, b"", 1).strip():
        raise FileError(config.E_ARCHIVO_VACIO, config.MSG_EMPTY)


def decode(raw: bytes) -> str:
    try:
        return raw.decode(config.CSV_ENCODING)
    except UnicodeDecodeError:
        raise FileError(config.E_CODIFICACION, config.MSG_ENCODING) from None


def parse_rows(text: str) -> tuple[list[str], list[Row]]:
    """Devuelve (cabecera recortada, filas de datos). Una única línea vacía final se ignora."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=config.CSV_DELIMITER, strict=True)
    try:
        # Se lee como mucho cabecera + límite + línea final + 1 para detectar el exceso.
        rows = list(islice(enumerate(reader, start=config.HEADER_ROW), config.MAX_DATA_ROWS + config.ROW_READ_SLACK))
    except csv.Error as exc:
        raise FileError(config.E_FORMATO, config.MSG_BAD_CSV.format(detail=exc)) from None
    if rows and rows[-1][1] == []:
        rows.pop()
    if len(rows) - 1 > config.MAX_DATA_ROWS:
        raise FileError(config.E_TAMANO, config.MSG_TOO_MANY_ROWS.format(max=config.MAX_DATA_ROWS))
    if len(rows) < 2:
        raise FileError(config.E_ARCHIVO_VACIO, config.MSG_ONLY_HEADER)
    return [name.strip() for name in rows[0][1]], rows[1:]


def header_issues(header: Sequence[str], expected: Sequence[str]) -> Iterator[HeaderIssue]:
    """Cabecera exacta: duplicadas, desconocidas, faltantes y orden. Perezoso y O(n)."""
    counts = Counter(header)
    known = set(expected)
    found = False
    for name, times in counts.items():
        if times > 1:
            found = True
            yield HeaderIssue(name, config.E_CABECERA, config.MSG_HEADER_DUPLICATE, name)
    for name in header:
        if name not in known:
            found = True
            yield HeaderIssue(name, config.E_COLUMNA_DESCONOCIDA, config.MSG_HEADER_UNKNOWN, name)
    for name in expected:
        if name not in counts:
            found = True
            yield HeaderIssue(name, config.E_CABECERA, config.MSG_HEADER_MISSING)
    if not found and list(header) != list(expected):
        yield HeaderIssue(None, config.E_CABECERA, config.MSG_HEADER_ORDER.format(expected=",".join(expected)))


def row_width_issue(fields: Sequence[str], width: int) -> str | None:
    """Mensaje si la fila no tiene `width` campos (o está vacía); None si está bien."""
    if not fields:
        return config.MSG_ROW_EMPTY
    if len(fields) != width:
        return config.MSG_ROW_WIDTH.format(found=len(fields), expected=width)
    return None

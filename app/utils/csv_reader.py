"""Lector CSV seguro: tipo de archivo, tamaño, UTF-8/BOM, filas y columnas."""
import csv
import io
import re
from typing import BinaryIO

from app.core import config
from app.utils.errors import FileError

Row = tuple[int, list[str]]   # (número de fila como en Excel, campos)


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
    match = pattern.match(filename or "")
    if not match:
        raise FileError(config.E_NOMBRE_ARCHIVO, f"El nombre debe cumplir {pattern.pattern}")
    return match


def check_raw(filename: str | None, raw: bytes) -> None:
    """Extensión, tamaño, binario y vacío (en este orden)."""
    if not (filename or "").lower().endswith(config.CSV_EXTENSION):
        raise FileError(config.E_TIPO_ARCHIVO, "La extensión debe ser .csv")
    if len(raw) > config.MAX_FILE_BYTES:
        raise FileError(config.E_TAMANO, f"Máximo {config.MAX_FILE_BYTES // (1024 * 1024)} MB")
    if config.NUL_BYTE in raw or raw.startswith(config.BINARY_MAGIC_PREFIXES):
        raise FileError(config.E_TIPO_ARCHIVO, "El contenido es binario, no texto CSV")
    if not raw.strip():
        raise FileError(config.E_ARCHIVO_VACIO, "El archivo está vacío")


def decode(raw: bytes) -> str:
    try:
        return raw.decode(config.CSV_ENCODING)
    except UnicodeDecodeError:
        raise FileError(config.E_CODIFICACION, "No es UTF-8 válido") from None


def parse_rows(text: str) -> tuple[list[str], list[Row]]:
    """Devuelve (cabecera, filas de datos). Una única línea vacía final se ignora."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=config.CSV_DELIMITER, strict=True)
    rows: list[Row] = []
    try:
        for fields in reader:
            rows.append((reader.line_num, fields))
            if len(rows) > config.MAX_DATA_ROWS + 1:
                raise FileError(config.E_TAMANO, f"Máximo {config.MAX_DATA_ROWS} filas de datos")
    except csv.Error as exc:
        raise FileError(config.E_FORMATO, f"CSV mal formado: {exc}") from None
    if rows and rows[-1][1] == []:
        rows.pop()
    if not rows or len(rows) < 2:
        raise FileError(config.E_ARCHIVO_VACIO, "El archivo solo tiene cabecera o está vacío")
    header = [name.strip() for name in rows[0][1]]
    if len(header) > config.MAX_COLUMNS:
        raise FileError(config.E_NUM_COLUMNAS, f"Máximo {config.MAX_COLUMNS} columnas")
    return header, rows[1:]


def read_csv_bytes(filename: str | None, raw: bytes) -> tuple[list[str], list[Row]]:
    check_raw(filename, raw)
    return parse_rows(decode(raw))

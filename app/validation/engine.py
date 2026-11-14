"""Validador genérico: recorre el esquema declarativo de un tipo y aplica la coherencia."""
from dataclasses import dataclass, field
from typing import Any

from app.core import config as c
from app.schemas.report import Report
from app.utils import checks, csv_reader, dates, numbers, text
from app.utils.errors import CellError, FileError
from app.validation.coherence import COHERENCE, Context, Record
from app.validation.collector import Collector
from app.validation.specs import DATE, DECIMAL, ENUM, REGEX, SCHEMAS, TEXT, URL, ColumnSpec


@dataclass
class ValidationResult:
    report: Report
    records: list[Record] = field(default_factory=list)
    http_status: int = 200
    content: bytes = b""            # bytes sin BOM, listos para guardar


def validate_cell(spec: ColumnSpec, value: str) -> Any:
    """Valida una celda ya recortada y devuelve su valor tipado."""
    if spec.kind == DECIMAL:
        return numbers.validate_decimal(value, spec.lo, spec.hi, spec.hi_inclusive)
    # Todo lo que no es número se revisa contra control, <>, y fórmulas.
    text.check_safe_chars(value)
    if spec.kind == TEXT:
        text.check_length(value, spec.min_len, spec.max_len)
        return value
    if spec.kind == DATE:
        return dates.parse_utc(value)
    if spec.kind == ENUM:
        return checks.validate_enum(value, spec.choices)
    if spec.kind == REGEX:
        return checks.validate_regex(value, spec.pattern)
    if spec.kind == URL:
        text.check_length(value, spec.min_len, spec.max_len)
        return checks.validate_https_url(value)
    raise ValueError(f"Tipo de columna desconocido: {spec.kind}")


def _check_header(header: list[str], schema: list[ColumnSpec], out: Collector) -> None:
    expected = [s.name for s in schema]
    for name in dict.fromkeys(n for n in header if header.count(n) > 1):
        out.error(c.HEADER_ROW, name, c.E_CABECERA, "Columna duplicada", name)
    for name in header:
        if name not in expected:
            out.error(c.HEADER_ROW, name, c.E_COLUMNA_DESCONOCIDA, "Columna que no existe en el estándar", name)
    for name in expected:
        if name not in header:
            out.error(c.HEADER_ROW, name, c.E_CABECERA, "Falta la columna obligatoria", None)
    if not out.has_errors and header != expected:
        out.error(c.HEADER_ROW, None, c.E_CABECERA, "Orden incorrecto; esperado: " + ",".join(expected))


def _check_rows(rows, schema: list[ColumnSpec], out: Collector) -> list[Record]:
    records: list[Record] = []
    unique_seen: dict[str, set] = {s.name: set() for s in schema if s.unique}
    for row_no, fields in rows:
        if len(fields) != len(schema):
            msg = "Fila vacía" if not fields else f"Tiene {len(fields)} campos; se esperaban {len(schema)}"
            out.error(row_no, None, c.E_NUM_COLUMNAS, msg)
            continue
        values: dict[str, Any] = {}
        row_ok = True
        for spec, raw in zip(schema, fields):
            value = raw.strip()
            if not value:
                if spec.required:
                    out.error(row_no, spec.name, c.E_REQUERIDO, "Campo obligatorio vacío")
                    row_ok = False
                values[spec.name] = spec.default
                continue
            try:
                values[spec.name] = validate_cell(spec, value)
            except CellError as exc:
                out.error(row_no, spec.name, exc.code, exc.message, value)
                row_ok = False
                continue
            if spec.unique:
                if value in unique_seen[spec.name]:
                    out.error(row_no, spec.name, c.E_DUPLICADO, "Valor repetido", value)
                    row_ok = False
                unique_seen[spec.name].add(value)
        if row_ok:
            records.append(Record(row_no, values))
    return records


def validate_csv(tipo: str, filename: str | None, raw: bytes, ctx: Context | None = None) -> ValidationResult:
    """Valida un archivo completo. No guarda nada."""
    ctx = ctx or Context()
    name = filename or ""
    out = Collector()
    try:
        csv_reader.check_raw(filename, raw)
        match = csv_reader.check_filename(filename, c.FILENAME_PATTERNS[tipo])
        header, rows = csv_reader.parse_rows(csv_reader.decode(raw))
        if tipo == c.TIPO_EFEMERIDES:
            ctx.file_site_id = match.group(1)
    except FileError as exc:
        out.error(c.HEADER_ROW, None, exc.code, exc.message)
        return ValidationResult(_report(tipo, name, out, 0), http_status=exc.http_status)

    schema = SCHEMAS[tipo]
    _check_header(header, schema, out)
    records: list[Record] = []
    if not out.has_errors:
        records = _check_rows(rows, schema, out)
    if not out.has_errors:
        COHERENCE[tipo](records, ctx, out)
    status = c.HTTP_DEFAULT_ERROR if out.has_errors else 200
    content = raw[len(b"\xef\xbb\xbf"):] if raw.startswith(b"\xef\xbb\xbf") else raw
    return ValidationResult(_report(tipo, name, out, len(rows)), records, status, content)


def _report(tipo: str, name: str, out: Collector, total_rows: int) -> Report:
    return Report(
        archivo=name, tipo=tipo, valido=not out.has_errors, total_filas=total_rows,
        total_errores=out.total_errors, errores=out.errors, advertencias=out.warnings,
    )

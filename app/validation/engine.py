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
    http_status: int = c.HTTP_OK
    content: bytes = b""            # bytes sin BOM, listos para guardar
    target_filename: str = ""       # nombre seguro (del patrón validado), nunca el crudo


def validate_cell(spec: ColumnSpec, value: str) -> Any:
    """Valida una celda ya recortada y no vacía; devuelve su valor tipado."""
    if spec.kind == DECIMAL:
        return numbers.validate_decimal(value, spec.lo, spec.hi, spec.hi_inclusive)
    text.check_not_null_token(value)
    text.check_safe_chars(value)   # control, <>, fórmulas
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
    for issue in csv_reader.header_issues(header, [s.name for s in schema]):
        out.error(c.HEADER_ROW, issue.column, issue.code, issue.message, issue.value)


def _check_rows(rows, schema: list[ColumnSpec], out: Collector) -> list[Record]:
    records: list[Record] = []
    unique_seen: dict[str, set] = {s.name: set() for s in schema if s.unique}
    for row_no, fields in rows:
        width_problem = csv_reader.row_width_issue(fields, len(schema))
        if width_problem:
            out.error(row_no, None, c.E_NUM_COLUMNAS, width_problem)
            continue
        values: dict[str, Any] = {}
        row_ok = True
        for spec, raw in zip(schema, fields):
            value = raw.strip()
            if not value:
                if spec.required:
                    out.error(row_no, spec.name, c.E_REQUERIDO, c.MSG_REQUIRED)
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
                    out.error(row_no, spec.name, c.E_DUPLICADO, c.MSG_DUPLICATE_VALUE, value)
                    row_ok = False
                unique_seen[spec.name].add(value)
        if row_ok:
            records.append(Record(row_no, values))
    return records


def validate_csv(tipo: str, filename: str | None, raw: bytes, ctx: Context | None = None) -> ValidationResult:
    """Valida un archivo completo. No guarda nada."""
    ctx = ctx or Context()
    shown_name = text.sanitize_filename(filename)
    out = Collector()
    try:
        csv_reader.check_raw(filename, raw)
        match = csv_reader.check_filename(filename, c.FILENAME_PATTERNS[tipo])
        header, rows = csv_reader.parse_rows(csv_reader.decode(raw))
    except FileError as exc:
        out.error(c.HEADER_ROW, None, exc.code, exc.message)
        return ValidationResult(_report(tipo, shown_name, out, 0), http_status=exc.http_status)
    if tipo == c.TIPO_EFEMERIDES:
        ctx.file_site_id = match.group(1)

    schema = SCHEMAS[tipo]
    _check_header(header, schema, out)
    records: list[Record] = []
    if not out.has_errors:
        records = _check_rows(rows, schema, out)
    if not out.has_errors:
        COHERENCE[tipo](records, ctx, out)
    status = c.HTTP_DEFAULT_ERROR if out.has_errors else c.HTTP_OK
    content = raw.removeprefix(c.UTF8_BOM)
    return ValidationResult(_report(tipo, shown_name, out, len(rows)), records, status, content, match.group(0))


def _report(tipo: str, name: str, out: Collector, total_rows: int) -> Report:
    return Report(
        archivo=name, tipo=tipo, valido=not out.has_errors, total_filas=total_rows,
        total_errores=out.total_errors, errores=out.errors, advertencias=out.warnings,
    )

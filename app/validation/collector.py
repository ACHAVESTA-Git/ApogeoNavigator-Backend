from app.core import config
from app.schemas.report import Issue


class Collector:
    """Acumula errores y advertencias; guarda como mucho MAX_REPORTED_ERRORS pero cuenta todos."""

    def __init__(self) -> None:
        self.errors: list[Issue] = []
        self.warnings: list[Issue] = []
        self.total_errors = 0

    @staticmethod
    def _issue(fila, columna, codigo, mensaje, valor) -> Issue:
        shown = None if valor is None else str(valor)[: config.MAX_VALUE_CHARS]
        return Issue(fila=fila, columna=columna, codigo=codigo, mensaje=mensaje, valor=shown)

    def error(self, fila: int, columna: str | None, codigo: str, mensaje: str, valor=None) -> None:
        self.total_errors += 1
        if len(self.errors) < config.MAX_REPORTED_ERRORS:
            self.errors.append(self._issue(fila, columna, codigo, mensaje, valor))

    def warning(self, fila: int, columna: str | None, codigo: str, mensaje: str, valor=None) -> None:
        if len(self.warnings) < config.MAX_REPORTED_ERRORS:
            self.warnings.append(self._issue(fila, columna, codigo, mensaje, valor))

    @property
    def has_errors(self) -> bool:
        return self.total_errors > 0

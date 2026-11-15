from app.core import config


class ValidationError(Exception):
    """Base común de los errores de validación."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CellError(ValidationError):
    """Error de un valor (celda) o de una regla de coherencia; se acumula y se sigue."""


class FileError(ValidationError):
    """Error que impide seguir leyendo el archivo; se devuelve de inmediato."""

    @property
    def http_status(self) -> int:
        return config.HTTP_BY_CODE.get(self.code, config.HTTP_DEFAULT_ERROR)

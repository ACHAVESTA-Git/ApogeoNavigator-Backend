from app.core import config


class CellError(Exception):
    """Error de validación de un valor (celda) o de una regla de archivo."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FileError(CellError):
    """Error que impide seguir leyendo el archivo (se devuelve de inmediato)."""

    @property
    def http_status(self) -> int:
        return config.HTTP_BY_CODE.get(self.code, config.HTTP_DEFAULT_ERROR)

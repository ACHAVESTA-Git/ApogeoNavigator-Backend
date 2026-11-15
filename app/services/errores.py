"""Error de negocio de un servicio con su código HTTP; el manejador central lo convierte en la respuesta estandarizada.

Los servicios no importan FastAPI: lanzan `ErrorServicio` y el router (o el manejador) decide la respuesta.
"""


class ErrorServicio(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail

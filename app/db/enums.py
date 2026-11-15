import enum


class RolUsuario(enum.StrEnum):
    ANALISTA = "analista"
    REVISOR = "revisor"
    ADMIN = "admin"


class AccionAuditoria(enum.StrEnum):
    LOGIN = "LOGIN"
    LOGIN_FALLIDO = "LOGIN_FALLIDO"
    LOGOUT = "LOGOUT"
    CARGA_VALIDADA = "CARGA_VALIDADA"
    CARGA_GUARDADA = "CARGA_GUARDADA"
    CARGA_RECHAZADA = "CARGA_RECHAZADA"
    CAMBIO_ROL = "CAMBIO_ROL"
    CAMBIO_DATOS = "CAMBIO_DATOS"
    VER_PROYECCION = "VER_PROYECCION"


class TipoCarga(enum.StrEnum):
    SITES = "sites"
    EFEMERIDES = "efemerides"
    METRICAS = "metricas"

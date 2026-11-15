"""Servicio de carga de CSV: validar, auditar y guardar de forma atómica. Sin FastAPI.

`origen` es el objeto de la petición (solo se usa para el hash de IP y el user-agent de la auditoría).
"""
from typing import BinaryIO

from app.core import config
from app.db.enums import AccionAuditoria
from app.mappers.carga import guardado_out
from app.schemas.report import SaveResult
from app.security.pqc import ErrorClavesPQC
from app.services import datastore, storage
from app.services.carga.servicio import Revision
from app.services.errores import ErrorServicio
from app.utils import auditoria_util
from app.utils.csv_reader import read_limited
from app.validation.engine import validate_csv


class CargaServiceImpl:
    """`auditoria`, `datos` y `almacen` son los módulos reales por defecto; se sustituyen por falsos en las pruebas."""

    def __init__(self, auditoria=auditoria_util, datos=datastore, almacen=storage, *, actor: str = config.ACTOR_API_KEY, usuario_id=None):
        self.actor, self.usuario_id = actor, usuario_id          # quién sube: el usuario de la sesión o la API key
        self.auditoria = auditoria
        self.datos = datos
        self.almacen = almacen

    def revisar(self, tipo: str, nombre: str | None, archivo: BinaryIO) -> Revision:
        if tipo not in config.TIPOS_ARCHIVO:
            raise ErrorServicio(config.HTTP_NOT_FOUND, config.MSG_UNKNOWN_TIPO.format(tipos=list(config.TIPOS_ARCHIVO)))
        bruto = read_limited(archivo)
        return Revision(validate_csv(tipo, nombre, bruto, self.almacen.build_context()), bruto)

    def auditar(self, tipo: str, revision: Revision, accion: AccionAuditoria, origen: object) -> None:
        """Registra la carga (validada o rechazada). En producción, si no se puede firmar: 503.

        Si el archivo falló antes de leerse entero (tamaño, binario, codificación, nombre...), `content` va vacío:
        se audita lo realmente leído. Si se cortó por superar el límite, no se calcula el hash (ver `registrar_carga`).
        """
        resultado = revision.resultado
        try:
            self.auditoria.registrar_carga(tipo, resultado.report.archivo, resultado.content or revision.bruto,
                                           resultado.report.total_filas, resultado.report.valido,
                                           resultado.report.total_errores, accion, actor=self.actor, usuario_id=self.usuario_id, request=origen,
                                           truncado=len(revision.bruto) > config.MAX_FILE_BYTES)
        except self.auditoria.ErrorAuditoria:
            raise ErrorServicio(config.HTTP_UNAVAILABLE, config.MSG_AUDIT_UNAVAILABLE) from None

    def guardar_carga(self, tipo: str, revision: Revision, origen: object) -> SaveResult:
        """Guarda un archivo ya validado y sin errores, de forma atómica, conservando la versión anterior en _historial/.

        El evento de auditoría se escribe ANTES de guardar y se confirma después: si firmar el manifiesto o el evento
        falla, no se guarda nada y la respuesta es 503.
        """
        resultado = revision.resultado
        try:
            with self.auditoria.unidad_de_trabajo() as db:
                registro = self.auditoria.registrar_carga(
                    tipo, resultado.target_filename, resultado.content, resultado.report.total_filas, True, 0,
                    AccionAuditoria.CARGA_GUARDADA, actor=self.actor, usuario_id=self.usuario_id, request=origen, ruta_almacenamiento=resultado.target_filename,
                    db=db) if db else None
                copia = self.datos.guardar(resultado.target_filename, resultado.content)
                if registro and copia:
                    registro.carga.ruta_historial = copia.name
        except self.auditoria.ErrorAuditoria:
            raise ErrorServicio(config.HTTP_UNAVAILABLE, config.MSG_AUDIT_UNAVAILABLE) from None
        except ErrorClavesPQC:
            raise ErrorServicio(config.HTTP_UNAVAILABLE, config.MSG_SIGNING_UNAVAILABLE) from None
        except self.datos.DataConflict:
            raise ErrorServicio(config.HTTP_CONFLICT, config.MSG_DATA_CONFLICT) from None
        return guardado_out(resultado.report, copia)

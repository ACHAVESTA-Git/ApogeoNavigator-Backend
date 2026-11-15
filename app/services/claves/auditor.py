"""Auditoría de los cambios de claves: se registra en la misma transacción y, si no se puede, no se hace el cambio."""
from sqlalchemy.orm import Session

from app.core import config as c
from app.db.enums import AccionAuditoria
from app.models.claves import ClaveUsuario
from app.services.errores import ErrorServicio
from app.utils import auditoria_util


class AuditorSql:
    """Escribe en la cadena firmada usando la sesión de la petición (estricto: sin firma, 503)."""

    def __init__(self, db: Session):
        self.db = db

    def registrar(self, operacion: str, clave: ClaveUsuario, motivo: str | None = None, extra: dict | None = None) -> None:
        detalle = {"operacion": operacion, "clave_id": str(clave.id), "usuario_id": str(clave.usuario_id),
                   "tipo": clave.tipo.value, "algoritmo": clave.algoritmo, "huella": clave.huella_sha3_256}
        if motivo:
            detalle["motivo"] = motivo
        detalle.update(extra or {})
        try:
            auditoria_util.registrar_evento(AccionAuditoria.CAMBIO_DATOS, c.ACTOR_ADMIN,
                                            recurso=c.KEYS_AUDIT_RESOURCE.format(id=clave.id), detalle=detalle, db=self.db,
                                            estricto=True)
        except auditoria_util.ErrorAuditoria:
            raise ErrorServicio(c.HTTP_UNAVAILABLE, c.MSG_KEYS_AUDIT_FAILED) from None

"""Auditoría del login con la cadena firmada. Los fallos se confirman al momento: la petición termina en error y su
transacción se desharía, pero el intento fallido tiene que quedar registrado."""
from sqlalchemy.orm import Session

from app.core import config as c
from app.utils import auditoria_util


class AuditorSql:
    def __init__(self, db: Session):
        self.db = db

    def login(self, usuario_id, origen) -> None:
        auditoria_util.registrar_login(usuario_id, c.ACTOR_USER, request=origen, db=self.db)

    def login_fallido(self, motivo: str, origen) -> None:
        auditoria_util.registrar_login_fallido(c.ACTOR_ANONYMOUS, request=origen, motivo=motivo, db=self.db)
        self.db.commit()

    def logout(self, usuario_id, origen) -> None:
        auditoria_util.registrar_evento(auditoria_util.AccionAuditoria.LOGOUT, c.ACTOR_USER, recurso=str(usuario_id),
                                        request=origen, usuario_id=usuario_id, db=self.db)

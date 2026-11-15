"""Entidades de usuario y clave a DTO del login. Solo datos públicos."""
from app.db.models import Usuario
from app.models.claves import ClaveUsuario
from app.schemas.auth import ClaveResumenOut, UsuarioActualOut


def usuario_actual(usuario: Usuario, claves: list[ClaveUsuario]) -> UsuarioActualOut:
    return UsuarioActualOut(id=usuario.id, nombre=usuario.nombre, rol=usuario.rol, claves=[
        ClaveResumenOut(id=k.id, tipo=k.tipo, algoritmo=k.algoritmo, estado=k.estado, huella_sha3_256=k.huella_sha3_256)
        for k in claves])

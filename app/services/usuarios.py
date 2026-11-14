"""Usuarios con el email cifrado en reposo. Toda lectura y escritura del email pasa por aquí.

El email se normaliza (minúsculas, sin espacios) y se guarda cifrado (`email_cifrado`, ML-KEM-1024 + AES-GCM) junto a su
HMAC-SHA256 (`email_hash`) para búsquedas exactas sin descifrar. Rotación de claves: ver `rotar_cifrado`.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import RolUsuario
from app.db.models import Usuario
from app.security import gestor_claves as gk


def crear_usuario(session: Session, email: str, nombre: str, rol: RolUsuario = RolUsuario.ANALISTA, activo: bool = True,
                  **campos) -> Usuario:
    """Crea (sin commit) un usuario con el email cifrado. Un email repetido falla con IntegrityError por email_hash único."""
    usuario = Usuario(email_cifrado=gk.cifrar_email(email), email_hash=gk.email_hash(email), nombre=nombre, rol=rol,
                      activo=activo, **campos)
    session.add(usuario)
    return usuario


def buscar_por_email(session: Session, email: str) -> Usuario | None:
    """Búsqueda exacta (sin distinguir mayúsculas). Durante una rotación de EMAIL_HASH_KEY prueba también las claves anteriores."""
    return session.execute(select(Usuario).where(Usuario.email_hash.in_(gk.email_hash_candidatos(email)))).scalar_one_or_none()


def email_de(usuario: Usuario) -> str:
    return gk.descifrar_email(usuario.email_cifrado)


@dataclass
class ResultadoRotacion:
    reencriptados: int = 0       # blobs cifrados con una clave KEM anterior que se pasaron a la actual
    rehasheados: int = 0         # email_hash recalculado con la clave de hash actual


def rotar_cifrado(session: Session) -> ResultadoRotacion:
    """Lote de rotación: vuelve a cifrar con la clave KEM actual y recalcula el hash con la clave actual.

    Pasos: 1) despliega la clave nueva en PQC_KEM_PRIVATE_KEY / EMAIL_HASH_KEY y las viejas en las variables `_ANTERIORES`;
    2) ejecuta este lote; 3) cuando devuelva 0 y 0, retira las anteriores. No hace commit.
    """
    resultado = ResultadoRotacion()
    for usuario in session.execute(select(Usuario)).scalars():
        email = gk.descifrar_email(usuario.email_cifrado)
        if gk.necesita_reencriptar(usuario.email_cifrado):
            usuario.email_cifrado = gk.reencriptar_email(usuario.email_cifrado)
            resultado.reencriptados += 1
        actual = gk.email_hash(email)
        if usuario.email_hash != actual:
            usuario.email_hash = actual
            resultado.rehasheados += 1
    return resultado

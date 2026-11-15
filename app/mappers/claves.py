"""Conversión de las entidades de claves a sus DTO. Nunca incluye material privado (no existe en la base)."""
from app.core import config as c
from app.db.models import Usuario
from app.models.claves import ClaveUsuario
from app.schemas.claves import ClaveEntregada, ClaveOut, PublicaOut


def clave_out(clave: ClaveUsuario, usuario: Usuario | None = None, email: str | None = None) -> ClaveOut:
    """`email` ya viene descifrado: el mapper no toca criptografía."""
    return ClaveOut(
        id=clave.id, usuario_id=clave.usuario_id, tipo=clave.tipo, algoritmo=clave.algoritmo, estado=clave.estado,
        huella_sha3_256=clave.huella_sha3_256, tamano_publica=clave.tamano_publica, tamano_privada=clave.tamano_privada,
        tamano_privada_fips=c.TAMANO_PRIVADA_FIPS[clave.tipo.value], creada_utc=clave.creada_utc,
        revocada_utc=clave.revocada_utc, motivo_revocacion=clave.motivo_revocacion, reemplazada_por=clave.reemplazada_por,
        backups=clave.backups, usuario_email=email, usuario_nombre=usuario.nombre if usuario else None)


def clave_entregada(base: ClaveOut, publica_pem: str, privada_pem: str) -> ClaveEntregada:
    return ClaveEntregada(**base.model_dump(), clave_publica_pem=publica_pem, privada_pem=privada_pem,
                          nombre_archivo_privada=c.KEYS_PRIVATE_FILENAME.format(
                              tipo=base.tipo.value, huella=base.huella_sha3_256[:c.KEYS_FILE_FINGERPRINT_CHARS]))


def nombre_archivo_publica(clave: ClaveUsuario | PublicaOut) -> str:
    """Vale para la entidad y para `PublicaOut`: solo usa `tipo` y `huella_sha3_256`."""
    return c.KEYS_PUBLIC_FILENAME.format(tipo=clave.tipo.value, huella=clave.huella_sha3_256[:c.KEYS_FILE_FINGERPRINT_CHARS])

"""Semilla de usuarios de prueba (analista, revisor, admin) con sus claves post-cuánticas.

- Idempotente: solo crea los que no existen (por email); no toca contraseñas ni claves de los que ya existen.
- Por cada usuario nuevo: contraseña aleatoria (Argon2id en la base) y sus 2 pares (ML-KEM-1024 y ML-DSA-65) con el gestor
  de claves. El servidor solo guarda las públicas: las privadas y la contraseña se entregan UNA vez, en el archivo de
  credenciales (permisos 600, fuera del repositorio), que se escribe ANTES de confirmar la base. Nunca se imprimen ni se registran.
"""
import os
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core import config as c
from app.db.enums import RolUsuario
from app.repositories.claves import SqlClavesRepository
from app.schemas.claves import GenerarIn
from app.security import passwords
from app.services import usuarios
from app.services.claves import ClavesServiceImpl, EntregaPendiente
from app.services.claves.auditor import AuditorSql


@dataclass
class ResultadoSemilla:
    creados: list[str] = field(default_factory=list)       # emails creados
    existentes: list[str] = field(default_factory=list)    # emails que ya estaban (no se tocan)


def email_de(rol: str) -> str:
    return f"{rol}@{c.SEED_EMAIL_DOMAIN}"


def _abrir_credenciales(destino: Path):
    """Abre el archivo de credenciales con permisos 600 desde el primer byte y sin seguir enlaces simbólicos.

    Nuevo: O_CREAT | O_EXCL (nunca pisa nada). Ya existente (segunda ejecución que añade usuarios que faltan): se abre para
    añadir, solo si es un archivo normal y es nuestro. En los dos casos `fchmod` sobre el descriptor: sin carreras con la ruta.
    """
    base = os.O_WRONLY | os.O_NOFOLLOW
    try:
        fd = os.open(destino, base | os.O_CREAT | os.O_EXCL, c.SEED_CREDENTIALS_MODE)
    except FileExistsError:
        fd = os.open(destino, base | os.O_APPEND)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            os.close(fd)
            raise PermissionError(c.MSG_SEED_FILE_UNSAFE)
    os.fchmod(fd, c.SEED_CREDENTIALS_MODE)
    return os.fdopen(fd, "a", encoding="utf-8")


def _bloque(email: str, rol: str, password: str, entrega: EntregaPendiente) -> str:
    lineas = [f"[{email}]", f"rol: {rol}", f"contrasena: {password}", ""]
    for clave in entrega.entrega.claves:
        lineas += [f"# {clave.algoritmo} ({clave.tipo.value}) - huella SHA3-256 de la publica: {clave.huella_sha3_256}",
                   f"# archivo sugerido: {clave.nombre_archivo_privada}", clave.privada_pem.strip(), ""]
    return "\n".join(lineas) + "\n"


def _guardar(archivo, texto: str) -> None:
    archivo.write(texto)
    archivo.flush()
    os.fsync(archivo.fileno())


def sembrar(session: Session, destino: Path) -> ResultadoSemilla:
    """Crea los usuarios que falten. Cada usuario y sus claves se confirman juntos; el archivo se abre solo si hay algo nuevo."""
    resultado = ResultadoSemilla()
    archivo = None
    try:
        for rol, nombre in c.SEED_USERS:
            email = email_de(rol)
            if usuarios.buscar_por_email(session, email) is not None:
                resultado.existentes.append(email)
                continue
            if archivo is None:
                archivo = _abrir_credenciales(destino)
                archivo.write(f"# Credenciales de PRUEBA generadas el {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}. "
                              "NO SUBIR AL REPOSITORIO. Cada clave privada se entrega solo aquí.\n\n")
            password = secrets.token_urlsafe(c.SEED_PASSWORD_BYTES)
            usuario = usuarios.crear_usuario(session, email, nombre, rol=RolUsuario(rol), password_hash=passwords.hash_password(password))
            session.flush()
            servicio = ClavesServiceImpl(SqlClavesRepository(session), AuditorSql(session))
            # El bloque del usuario (contraseña y privadas) se escribe y se fuerza a disco ANTES de confirmar la base: si la
            # escritura falla no se confirma nada; si el commit falla, solo sobra un bloque de claves de un usuario que no existe.
            entrega = servicio.generar(GenerarIn(usuario_id=usuario.id),
                                       antes_de_confirmar=lambda ent: _guardar(archivo, _bloque(email, rol, password, ent)))
            servicio.cerrar_entrega(entrega)                                # cuenta la entrega y borra las privadas de memoria
            resultado.creados.append(email)
    finally:
        if archivo is not None:
            archivo.close()
    return resultado

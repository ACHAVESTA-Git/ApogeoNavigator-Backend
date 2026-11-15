"""Dependencias de FastAPI: construyen los servicios con sus repositorios y autentican las peticiones.

Es el único sitio que conoce las implementaciones. Autenticación de los endpoints protegidos:
- X-API-Key (scripts): sigue funcionando igual que antes.
- Cookie de sesión (personas): el rol debe estar permitido; en peticiones que cambian datos se exige además
  `X-Requested-With` o un `Origin` de confianza (defensa CSRF, además de SameSite=Strict).
"""
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import config as c
from app.core.security import is_valid_api_key, require_admin_key
from app.db import session as db_session
from app.db.models import Usuario
from app.db.session import get_db
from app.repositories.auth import SqlAuthRepository
from app.repositories.claves import SqlClavesRepository
from app.services.auth import AuthService, AuthServiceImpl
from app.services.auth.auditor import AuditorSql as AuditorAuth
from app.services.carga import CargaService, CargaServiceImpl
from app.services.claves import ClavesService, ClavesServiceImpl
from app.services.claves.auditor import AuditorSql


def get_auth_service(db: Session = Depends(get_db)) -> AuthService:
    return AuthServiceImpl(SqlAuthRepository(db), AuditorAuth(db))


def get_db_opcional() -> Iterator[Session | None]:
    """Sesión de BD si hay base configurada; None si no (la autenticación por API key no la necesita)."""
    if not db_session.db_enabled():
        yield None
        return
    with db_session.session_scope() as session:
        yield session


@dataclass
class Principal:
    """Quién hace la petición: un script con API key o una persona con sesión."""
    via: str
    usuario: Usuario | None = None

    def actor(self, por_defecto: str = c.ACTOR_ADMIN) -> tuple[str, "uuid.UUID | None"]:
        """(actor, usuario_id) para la auditoría: el usuario real si hay sesión; si no, el actor de la API key."""
        return (c.ACTOR_USER, self.usuario.id) if self.usuario is not None else (por_defecto, None)


def _token(request: Request) -> str | None:
    return request.cookies.get(c.AUTH_COOKIE_NAME)


def _origen_de_confianza(request: Request) -> bool:
    if request.headers.get(c.AUTH_CSRF_HEADER):
        return True
    origen = request.headers.get("origin")
    if not origen:
        return False
    patron = c.get_cors_origin_regex()
    return origen in c.get_cors_origins() or bool(patron and re.fullmatch(patron, origen))


def exigir_origen_de_confianza(request: Request) -> None:
    """CSRF: una página de otro sitio no puede añadir X-Requested-With sin permiso CORS ni falsificar su Origin."""
    if request.method not in c.AUTH_SAFE_METHODS and not _origen_de_confianza(request):
        raise HTTPException(status_code=c.HTTP_FORBIDDEN, detail=c.MSG_CSRF_FORBIDDEN)


def usuario_de_sesion(request: Request, db: Session | None) -> Usuario:
    """Usuario de la cookie de sesión o 401. No comprueba el rol."""
    servicio = AuthServiceImpl(SqlAuthRepository(db), AuditorAuth(db)) if db is not None else None
    usuario = servicio.autenticar(_token(request)) if servicio else None
    if usuario is None:
        raise HTTPException(status_code=c.HTTP_UNAUTHORIZED, detail=c.MSG_SESSION_INVALID)
    return usuario


def _por_sesion(request: Request, db: Session | None, roles: tuple[str, ...], sin_credenciales: str) -> Principal:
    if not _token(request) or db is None:
        raise HTTPException(status_code=c.HTTP_UNAUTHORIZED, detail=sin_credenciales)
    usuario = usuario_de_sesion(request, db)
    exigir_origen_de_confianza(request)
    if usuario.rol.value not in roles:
        raise HTTPException(status_code=c.HTTP_FORBIDDEN, detail=c.MSG_ROLE_FORBIDDEN)
    return Principal("sesion", usuario)


def autenticar_carga(request: Request, x_api_key: str | None = Header(default=None, alias=c.API_KEY_HEADER),
                     db: Session | None = Depends(get_db_opcional)) -> Principal:
    """Carga de CSV: API key de carga, o sesión con rol analista, revisor o admin."""
    if is_valid_api_key(x_api_key):
        return Principal("api_key")
    return _por_sesion(request, db, c.ROLES_CARGA, c.MSG_API_KEY_INVALID)


def autenticar_admin(request: Request, x_api_key: str | None = Header(default=None, alias=c.API_KEY_HEADER),
                     db: Session | None = Depends(get_db_opcional)) -> Principal:
    """Gestor de claves: API key de administración, o sesión con rol admin."""
    if x_api_key is None and _token(request):
        return _por_sesion(request, db, c.ROLES_ADMIN, c.MSG_KEYS_ADMIN_KEY)
    require_admin_key(x_api_key)
    return Principal("api_key")


def autenticar_solo_admin_sesion(request: Request, db: Session | None = Depends(get_db_opcional)) -> Principal:
    """Solo una persona con sesión de rol admin. La X-API-Key NO da acceso: es una credencial compartida de scripts, sin
    persona detrás, y la consulta se audita con el usuario real."""
    return _por_sesion(request, db, c.ROLES_ADMIN, c.MSG_PROJ_SESSION_REQUIRED)


def get_claves_service(db: Session = Depends(get_db), principal: "Principal" = Depends(autenticar_admin)) -> ClavesService:
    """El actor de la auditoría es quien hace el cambio: el usuario de la sesión o, con la API key, `admin-api-key`."""
    actor, usuario_id = principal.actor()
    return ClavesServiceImpl(SqlClavesRepository(db), AuditorSql(db, actor, usuario_id))


def get_carga_service(principal: "Principal" = Depends(autenticar_carga)) -> CargaService:
    actor, usuario_id = principal.actor(c.ACTOR_API_KEY)
    return CargaServiceImpl(actor=actor, usuario_id=usuario_id)


def sesion_valida(token: str) -> bool:
    """¿Hay una sesión vigente con este token? Para el guardia de subida (corre en un hilo)."""
    if not db_session.db_enabled():
        return False
    with db_session.session_scope() as db:
        return AuthServiceImpl(SqlAuthRepository(db), AuditorAuth(db)).autenticar(token) is not None

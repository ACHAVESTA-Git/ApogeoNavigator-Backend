"""Servicio de autenticación: login local con límite de intentos, sesión con token opaco y logout. Sin FastAPI.

- El token es aleatorio (256 bits); en la base solo está su SHA3-256. Se rota en cada login.
- Credenciales inválidas: siempre la misma respuesta, exista o no el email, y con el mismo coste de tiempo.
- Límite de intentos FALLIDOS por email y por IP (ventana deslizante en memoria, con tope de claves).
"""
import hashlib
import math
import secrets
from datetime import datetime, timedelta, timezone

from app.core import config as c
from app.db.models import Usuario
from app.mappers.auth import usuario_actual
from app.repositories.auth import AuthRepository
from app.schemas.auth import UsuarioActualOut
from app.security import passwords
from app.services.auth.servicio import Auditor, SesionNueva
from app.services.errores import ErrorServicio
from app.utils import auditoria_util
from app.utils.dates import as_utc
from app.utils.ratelimit import SlidingWindowLimiter
from app.utils.text import clean_text

_limite_email = SlidingWindowLimiter(*c.AUTH_LOGIN_LIMIT_EMAIL)
_limite_ip = SlidingWindowLimiter(*c.AUTH_LOGIN_LIMIT_IP)


def reiniciar_limites() -> None:
    _limite_email.reset()
    _limite_ip.reset()


def hash_token(token: str) -> str:
    return hashlib.sha3_256(token.encode("ascii", "ignore")).hexdigest()


def _normalizar(email: str) -> str:
    return email.strip().lower()


class AuthServiceImpl:
    def __init__(self, repo: AuthRepository, auditor: Auditor, ahora=lambda: datetime.now(timezone.utc),
                 limite_email: SlidingWindowLimiter = _limite_email, limite_ip: SlidingWindowLimiter = _limite_ip):
        self.repo, self.auditor, self.ahora = repo, auditor, ahora
        self.limite_email, self.limite_ip = limite_email, limite_ip

    # ------------------------------------------------------------ login
    def login(self, email: str, password: str, origen: object, token_previo: str | None = None) -> SesionNueva:
        clave_email = hashlib.sha3_256(_normalizar(email).encode()).hexdigest()
        ip = auditoria_util.trusted_client_ip(origen)
        espera = max(self.limite_email.peek(clave_email), self.limite_ip.peek(ip))
        if espera > 0:
            self.auditor.login_fallido(c.MOTIVO_LOGIN_LIMITE, origen)
            raise ErrorServicio(c.HTTP_TOO_MANY_REQUESTS, c.MSG_LOGIN_TOO_MANY,
                                headers={c.RETRY_AFTER_HEADER: str(max(1, math.ceil(espera)))})
        usuario = self.repo.usuario_por_email(email)
        correcta = passwords.verify_password(usuario.password_hash if usuario else None, password)
        if usuario is None or not usuario.activo or not correcta:
            self.limite_email.hit(clave_email)
            self.limite_ip.hit(ip)
            self.auditor.login_fallido(c.MOTIVO_LOGIN_CREDENCIALES, origen)
            raise ErrorServicio(c.HTTP_UNAUTHORIZED, c.MSG_LOGIN_INVALID)
        self.limite_email.forget(clave_email)
        if token_previo:                                    # rotación: la cookie anterior deja de valer
            self._revocar(token_previo)
        if passwords.needs_rehash(usuario.password_hash):
            usuario.password_hash = passwords.hash_password(password)
        token = secrets.token_urlsafe(c.AUTH_TOKEN_BYTES)
        ahora = self.ahora()
        expira = ahora + timedelta(hours=c.AUTH_SESSION_HOURS)
        self.repo.crear_sesion(usuario.id, hash_token(token), expira, auditoria_util.hash_ip(auditoria_util.client_ip(origen)),
                               clean_text(_cabecera(origen, c.USER_AGENT_HEADER), c.DB_USER_AGENT_MAX))
        usuario.ultimo_login_utc = ahora
        self.auditor.login(usuario.id, origen)
        perfil = self.perfil(usuario)
        self.repo.commit()
        return SesionNueva(token=token, expira_utc=expira, usuario=perfil)

    # ------------------------------------------------------------ logout y sesión
    def logout(self, token: str | None, origen: object) -> None:
        """Idempotente: sin sesión o ya revocada no hace nada."""
        sesion = self._revocar(token) if token else None
        if sesion is not None:
            self.auditor.logout(sesion.usuario_id, origen)
        self.repo.commit()

    def autenticar(self, token: str | None) -> Usuario | None:
        """Usuario dueño de una sesión vigente (no revocada, no caducada, usuario activo); si no, None."""
        if not token:
            return None
        sesion = self.repo.sesion_por_hash(hash_token(token))
        if sesion is None or sesion.revocada or as_utc(sesion.expira_utc) <= self.ahora():
            return None
        usuario = sesion.usuario
        return usuario if usuario.activo else None

    def perfil(self, usuario: Usuario) -> UsuarioActualOut:
        return usuario_actual(usuario, self.repo.claves_activas(usuario.id))

    # ------------------------------------------------------------ internos
    def _revocar(self, token: str):
        sesion = self.repo.sesion_por_hash(hash_token(token))
        if sesion is None or sesion.revocada:
            return None
        sesion.revocada = True
        return sesion


def _cabecera(origen: object, nombre: str) -> str | None:
    headers = getattr(origen, "headers", None)
    return headers.get(nombre) if headers is not None else None

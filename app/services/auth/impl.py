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
_audit_limite_clave = SlidingWindowLimiter(1, c.AUTH_AUDIT_LIMIT_WINDOW_S)
_audit_limite_global = SlidingWindowLimiter(*c.AUTH_AUDIT_LIMIT_GLOBAL)


def reiniciar_limites() -> None:
    _limite_email.reset()
    _limite_ip.reset()
    _audit_limite_clave.reset()
    _audit_limite_global.reset()


def hash_token(token: str) -> str:
    return hashlib.sha3_256(token.encode("ascii", "ignore")).hexdigest()


def _normalizar(email: str) -> str:
    return email.strip().lower()


class AuthServiceImpl:
    def __init__(self, repo: AuthRepository, auditor: Auditor, ahora=lambda: datetime.now(timezone.utc),
                 limite_email: SlidingWindowLimiter = _limite_email, limite_ip: SlidingWindowLimiter = _limite_ip,
                 audit_limite_clave: SlidingWindowLimiter = _audit_limite_clave,
                 audit_limite_global: SlidingWindowLimiter = _audit_limite_global):
        self.repo, self.auditor, self.ahora = repo, auditor, ahora
        self.limite_email, self.limite_ip = limite_email, limite_ip
        self.audit_limite_clave, self.audit_limite_global = audit_limite_clave, audit_limite_global

    # ------------------------------------------------------------ login
    def login(self, email: str, password: str, origen: object, token_previo: str | None = None) -> SesionNueva:
        clave_email = hashlib.sha3_256(_normalizar(email).encode()).hexdigest()
        ip = auditoria_util.trusted_client_ip(origen)
        # El intento se RESERVA antes de verificar: una ráfaga concurrente no puede pasar el límite mientras se calcula el hash.
        espera = self.limite_email.hit(clave_email)
        if espera <= 0:
            espera = self.limite_ip.hit(ip)
            if espera > 0:
                self.limite_email.refund(clave_email)
        if espera > 0:
            self._auditar_limite(clave_email, origen)
            raise ErrorServicio(c.HTTP_TOO_MANY_REQUESTS, c.MSG_LOGIN_TOO_MANY,
                                headers={c.RETRY_AFTER_HEADER: str(max(1, math.ceil(espera)))})
        usuario = self.repo.usuario_por_email(email)
        try:
            correcta = passwords.verify_password(usuario.password_hash if usuario else None, password)
        except passwords.VerificadorOcupado:
            self.limite_email.refund(clave_email)           # no fue un intento del usuario: no le cuesta
            self.limite_ip.refund(ip)
            raise ErrorServicio(c.HTTP_UNAVAILABLE, c.MSG_LOGIN_BUSY,
                                headers={c.RETRY_AFTER_HEADER: str(c.AUTH_BUSY_RETRY_AFTER_S)}) from None
        if usuario is None or not usuario.activo or not correcta:
            self.auditor.login_fallido(c.MOTIVO_LOGIN_CREDENCIALES, origen)
            raise ErrorServicio(c.HTTP_UNAUTHORIZED, c.MSG_LOGIN_INVALID)
        self.limite_email.forget(clave_email)               # un login correcto no cuenta como intento fallido
        self.limite_ip.refund(ip)
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

    def _auditar_limite(self, clave_email: str, origen: object) -> None:
        """Cuando salta el límite se firma UN evento por clave y ventana (y pocos por minuto en total), no uno por cada 429:
        si no, el propio ataque llenaría la cadena de auditoría y gastaría CPU de firma."""
        if self.audit_limite_clave.hit(clave_email) <= 0 and self.audit_limite_global.hit("global") <= 0:
            self.auditor.login_fallido(c.MOTIVO_LOGIN_LIMITE, origen)

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

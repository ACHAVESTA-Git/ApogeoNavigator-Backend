"""Login local: e-mail y contraseña, sesión en cookie httpOnly. OAuth con Google queda como trabajo futuro."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.deps import exigir_origen_de_confianza, get_auth_service
from app.core import config as c
from app.schemas.auth import LoginIn, UsuarioActualOut
from app.schemas.common import ErrorOut
from app.services.auth import AuthService

router = APIRouter(prefix=c.AUTH_ROUTE_PREFIX, tags=[c.ROUTER_TAG_AUTH])


def _poner_cookie(response: Response, token: str) -> None:
    response.set_cookie(c.AUTH_COOKIE_NAME, token, max_age=c.AUTH_SESSION_HOURS * 3600, path=c.AUTH_COOKIE_PATH, httponly=True,
                        secure=c.is_production(), samesite=c.AUTH_COOKIE_SAMESITE)
    response.headers["Cache-Control"] = "no-store"


@router.post("/login", response_model=UsuarioActualOut, summary="Sign in with e-mail and password",
             responses={401: {"description": "Wrong credentials. The same answer whether or not the e-mail exists.", "model": ErrorOut},
                        429: {"description": "Too many failed attempts for this e-mail or IP; see `Retry-After`.", "model": ErrorOut}})
def login(cuerpo: LoginIn, request: Request, response: Response, servicio: AuthService = Depends(get_auth_service)):
    """Checks the credentials and opens a session: an opaque random token in an `httpOnly`, `SameSite=Strict` cookie
    (`Secure` in production) that lasts 8 hours. Only the hash of the token is stored. A previous session cookie is revoked
    (rotation). Failed attempts are limited per e-mail and per IP and recorded in the audit log."""
    nueva = servicio.login(cuerpo.email, cuerpo.password, request, request.cookies.get(c.AUTH_COOKIE_NAME))
    _poner_cookie(response, nueva.token)
    return nueva.usuario


@router.post("/logout", status_code=c.HTTP_NO_CONTENT, summary="Sign out", dependencies=[Depends(exigir_origen_de_confianza)])
def logout(request: Request, servicio: AuthService = Depends(get_auth_service)):
    """Revokes the session of the cookie and deletes it. Idempotent. Needs the `X-Requested-With` header (or a trusted
    `Origin`) as CSRF protection."""
    servicio.logout(request.cookies.get(c.AUTH_COOKIE_NAME), request)
    respuesta = Response(status_code=c.HTTP_NO_CONTENT)
    respuesta.delete_cookie(c.AUTH_COOKIE_NAME, path=c.AUTH_COOKIE_PATH, httponly=True, secure=c.is_production(),
                            samesite=c.AUTH_COOKIE_SAMESITE)
    return respuesta


@router.get("/me", response_model=UsuarioActualOut, summary="Current user",
            responses={401: {"description": "No session, or it expired or was revoked.", "model": ErrorOut}})
def me(request: Request, servicio: AuthService = Depends(get_auth_service)):
    """Id, name, role and the fingerprints of the user's active public keys. Private keys are never returned."""
    usuario = servicio.autenticar(request.cookies.get(c.AUTH_COOKIE_NAME))
    if usuario is None:
        raise HTTPException(status_code=c.HTTP_UNAUTHORIZED, detail=c.MSG_SESSION_INVALID)
    return servicio.perfil(usuario)

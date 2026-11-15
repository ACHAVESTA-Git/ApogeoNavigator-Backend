"""Proyección hasta jul-2028. Solo administradores con sesión; cada consulta queda en la auditoría."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import Principal, autenticar_solo_admin_sesion, get_db_opcional
from app.core import config as c
from app.db.enums import AccionAuditoria
from app.routers.comun import RESPONSES
from app.routers.lectura import DESDE, HASTA
from app.schemas.common import ErrorOut
from app.schemas.proyeccion_admin import ProjectionOut
from app.services import proyeccion_datos
from app.utils import auditoria_util

router = APIRouter(prefix=c.API_PREFIX, tags=[c.ROUTER_TAG_LECTURA])

AUTH_RESPONSES = {**RESPONSES,
                  c.HTTP_UNAUTHORIZED: {"description": "No active admin session.", "model": ErrorOut},
                  c.HTTP_FORBIDDEN: {"description": "The session's role is not admin.", "model": ErrorOut}}


@router.get(c.ROUTE_PROYECCION, response_model=ProjectionOut, responses=AUTH_RESPONSES,
            summary="Projected Sun and Earth sky and monthly metrics up to July 2028 (admin only)")
def proyeccion(request: Request,
               site_id: Annotated[list[str], Query(default_factory=list, description=f"Site to query; repeat it for 1 to {c.PROYECCION_MAX_SITIOS} sites.", examples=[["shackleton"]])],
               desde: Annotated[str | None, DESDE] = None, hasta: Annotated[str | None, HASTA] = None,
               paso_horas: Annotated[str | None, Query(description="Spacing of `serie` in hours: 1, 6 (default) or 24.", examples=["6"])] = None,
               principal: Principal = Depends(autenticar_solo_admin_sesion),
               db: Session | None = Depends(get_db_opcional)):
    """Projected series (Sun and Earth azimuth and elevation) and projected monthly metrics (% of Sun, % of Earth, traffic light
    and astronaut indicators) from `data/proyecciones/`, plus the model and its measured error.

    **Admin only, by session.** The API key does NOT give access: it is a shared script credential, not tied to a person, and every
    query is audited with the real user. Without a session: 401; with the analyst or reviewer role: 403.

    Defaults: `desde`/`hasta` span the whole projection. These are modelled values with a measured error, not ephemerides."""
    result = proyeccion_datos.projection(site_id, desde, hasta, paso_horas)
    actor, usuario_id = principal.actor()
    auditoria_util.registrar_evento(
        AccionAuditoria.VER_PROYECCION, actor, recurso=c.MSG_PROJ_AUDIT_RESOURCE, usuario_id=usuario_id, request=request, db=db,
        detalle={"sitios": [r[c.COL_SITE_ID] for r in result[c.KEY_RESULTADOS]], "desde": desde, "hasta": hasta,
                 "paso_horas": proyeccion_datos.parse_step(paso_horas)})
    if db is not None:
        db.commit()
    return result

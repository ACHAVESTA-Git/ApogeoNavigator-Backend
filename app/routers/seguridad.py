"""Clave pública PQC y manifiesto firmado de los datos. Sin autenticación: es información pública."""
import base64

from fastapi import APIRouter, HTTPException, Response

from app.core import config
from app.schemas.seguridad import ClavePublicaOut
from app.security import pqc

router = APIRouter(tags=[config.ROUTER_TAG_SECURITY])


@router.get(config.ROUTE_SECURITY_PUBKEY, response_model=ClavePublicaOut, summary="Public ML-DSA-65 key that signs the data")
def clave_publica() -> ClavePublicaOut:
    try:
        return ClavePublicaOut(algoritmo=pqc.ALGORITMO, estandar=pqc.ESTANDAR,
                               clave_publica_b64=base64.b64encode(pqc.clave_publica()).decode("ascii"),
                               huella_sha3_256=pqc.huella_clave_publica())
    except pqc.ErrorClavesPQC:
        raise HTTPException(status_code=config.HTTP_UNAVAILABLE, detail=config.MSG_SIGNING_UNAVAILABLE) from None


@router.get(config.ROUTE_SECURITY_MANIFEST, summary="Signed manifest of the data files (exact bytes of manifest.json)",
            responses={config.HTTP_OK: {"description": "Exact bytes of the signed manifest.json (not re-serialised, so the signature stays valid)",
                                        "content": {config.JSON_MEDIA_TYPE: {}}}})
def manifiesto() -> Response:
    """Devuelve data/manifest.json tal cual está. Cambia con cada carga: no se cachea."""
    path = config.get_data_dir() / pqc.MANIFEST_FILENAME
    try:
        body = path.read_bytes()
    except FileNotFoundError:
        raise HTTPException(status_code=config.HTTP_NOT_FOUND, detail=config.MSG_NO_MANIFEST) from None
    return Response(content=body, media_type=config.JSON_MEDIA_TYPE,
                    headers={config.CACHE_CONTROL_HEADER: config.CACHE_CONTROL_NO_CACHE})

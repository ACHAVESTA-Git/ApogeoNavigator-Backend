"""CORS configurable: el frontend (Cloudflare Pages, local o dominio propio) llama a la API desde otro origen."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import config


def add_cors(app: FastAPI) -> None:
    """Añade CORSMiddleware. Debe llamarse DESPUÉS de los demás middleware para quedar como el más externo
    y que también las respuestas 401/413 de upload_guard lleven las cabeceras CORS."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.get_cors_origins(),
        allow_origin_regex=config.get_cors_origin_regex(),
        allow_methods=list(config.CORS_ALLOW_METHODS),
        allow_headers=list(config.CORS_ALLOW_HEADERS),
        allow_credentials=config.CORS_ALLOW_CREDENTIALS,
        expose_headers=list(config.CORS_EXPOSE_HEADERS),
        max_age=config.CORS_MAX_AGE_SECONDS,
    )

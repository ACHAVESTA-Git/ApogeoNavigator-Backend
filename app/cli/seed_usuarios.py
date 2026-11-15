"""Comando: python -m app.cli.seed_usuarios [--salida RUTA] [--forzar-produccion]

Crea 3 usuarios de prueba (analista, revisor y admin, @apogeo.test) con contraseñas aleatorias y sus claves PQC.
Las credenciales y las claves privadas van al archivo de credenciales (permisos 600), nunca a la salida ni a los logs.
"""
import argparse
import os
import sys
from pathlib import Path

from app.core import config as c
from app.db import session as db_session
from app.services.semilla_usuarios import sembrar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.seed_usuarios", description=__doc__.splitlines()[0])
    parser.add_argument("--salida", type=Path, default=Path(os.getenv(c.ENV_SEED_CREDENTIALS_FILE) or c.SEED_CREDENTIALS_DEFAULT),
                        help=f"archivo de credenciales (por defecto {c.SEED_CREDENTIALS_DEFAULT} o ${c.ENV_SEED_CREDENTIALS_FILE})")
    parser.add_argument(c.FLAG_FORCE_PRODUCTION, action="store_true", help="permite ejecutarlo en producción")
    args = parser.parse_args(argv)
    if c.is_production() and not getattr(args, "forzar_produccion"):
        print(c.MSG_SEED_PRODUCTION, file=sys.stderr)
        return 2
    if not db_session.db_enabled():
        print(c.MSG_SEED_NO_DB, file=sys.stderr)
        return 2
    with db_session.session_scope() as session:
        resultado = sembrar(session, args.salida)
    for email in resultado.creados:
        print(f"creado: {email}")
    for email in resultado.existentes:
        print(f"ya existía: {email} (sin cambios)")
    if resultado.creados:
        print(f"Credenciales y claves privadas en {args.salida} (permisos 600). No lo subas al repositorio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

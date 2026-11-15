#!/bin/sh
# Crea los usuarios de prueba (analista, revisor, admin) y sus claves PQC. Ver `python -m app.cli.seed_usuarios --help`.
exec python -m app.cli.seed_usuarios "$@"

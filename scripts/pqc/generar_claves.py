"""Genera un par de claves ML-DSA-65 (FIPS 204) para PRODUCCIÓN.

Uso (desde ApogeoNavigator-Backend/):

    python scripts/pqc/generar_claves.py              # imprime PQC_PRIVATE_KEY y PQC_PUBLIC_KEY
    python scripts/pqc/generar_claves.py --solo-publica   # deriva la pública de PQC_PRIVATE_KEY
    python scripts/pqc/generar_claves.py --servidor       # PQC_KEM_PRIVATE_KEY y EMAIL_HASH_KEY (datos personales)

Las claves se imprimen por la salida estándar y NUNCA se escriben en disco. Redirígelas
directamente al gestor de secretos; no las pegues en el repositorio, en commits, en chats
ni en tickets. La clave pública no es secreta y puede publicarse.

Guardado en secretos (ejemplos; revisar proyecto y permisos antes de ejecutarlos):

    GCP:        printf %s "$PQC_PRIVATE_KEY" | gcloud secrets create pqc-private-key --data-file=-
    Cloudflare: wrangler secret put PQC_PRIVATE_KEY
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.security import gestor_claves, pqc  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
MSG_NEED_PRIVATE = f"Define {pqc.ENV_PRIVATE_KEY} para derivar la clave pública."
MSG_FINGERPRINT = "Huella SHA3-256 de la clave pública: {huella}"
MSG_WARNING = (
    "La clave privada solo aparece en la salida estándar. Guárdala en el gestor de secretos "
    "y no la subas al repositorio."
)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--solo-publica", action="store_true",
                        help=f"deriva la clave pública a partir de {pqc.ENV_PRIVATE_KEY}")
    parser.add_argument("--servidor", action="store_true",
                        help="genera PQC_KEM_PRIVATE_KEY y EMAIL_HASH_KEY (cifrado de datos personales)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.servidor:
        semilla, clave_hash = gestor_claves.generar_claves_servidor()
        print(f"{gestor_claves.ENV_KEM_PRIVATE}={semilla}")
        print(f"{gestor_claves.ENV_EMAIL_HASH_KEY}={clave_hash}")
        print(MSG_WARNING, file=sys.stderr)
        return EXIT_OK
    if args.solo_publica:
        privada = os.getenv(pqc.ENV_PRIVATE_KEY, "").strip()
        if not privada:
            print(MSG_NEED_PRIVATE, file=sys.stderr)
            return EXIT_ERROR
        publica_b64 = base64.b64encode(pqc.clave_publica_desde_semilla(privada)).decode("ascii")
        print(f"{pqc.ENV_PUBLIC_KEY}={publica_b64}")
        return EXIT_OK
    privada_b64, publica_b64 = pqc.generar_par()
    print(f"{pqc.ENV_PRIVATE_KEY}={privada_b64}")
    print(f"{pqc.ENV_PUBLIC_KEY}={publica_b64}")
    huella = pqc.huella_clave_publica(base64.b64decode(publica_b64))
    print(MSG_FINGERPRINT.format(huella=huella), file=sys.stderr)
    print(MSG_WARNING, file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

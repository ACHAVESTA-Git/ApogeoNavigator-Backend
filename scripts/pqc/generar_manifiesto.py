"""Genera y firma data/manifest.json (SHA3-256 de cada CSV + firma ML-DSA-65).

Uso (desde ApogeoNavigator-Backend/):

    python scripts/pqc/generar_manifiesto.py [--datos data]

Necesita PQC_PRIVATE_KEY (producción) o, solo en desarrollo, usa el par local de .pqc_dev/.
Un manifiesto firmado con claves de desarrollo NO debe publicarse ni subirse al repositorio:
nadie podría verificarlo con la huella oficial.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.security import pqc  # noqa: E402

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
EXIT_OK = 0
EXIT_ERROR = 1
MSG_NOT_A_DIR = "No existe la carpeta de datos: {ruta}"
MSG_DONE = "Manifiesto escrito en {ruta} ({n} archivos). Huella de la clave: {huella}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datos", type=Path, default=DEFAULT_DATA_DIR, help="carpeta con los CSV")
    args = parser.parse_args(argv)
    if not args.datos.is_dir():
        print(MSG_NOT_A_DIR.format(ruta=args.datos), file=sys.stderr)
        return EXIT_ERROR
    manifiesto = pqc.generar_manifiesto(args.datos)
    ruta = pqc.escribir_manifiesto(args.datos, manifiesto)
    print(MSG_DONE.format(ruta=ruta, n=len(manifiesto["archivos"]), huella=manifiesto["huella_clave_publica"]))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

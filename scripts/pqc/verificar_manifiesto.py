#!/usr/bin/env python3
"""Verificación INDEPENDIENTE del manifiesto firmado de los datos de Apogeo.

No importa código del proyecto: solo necesita Python 3.9+ y la librería `cryptography` >= 47
(`pip install "cryptography>=47"`). Sirve para que un tercero (p. ej. el jurado) compruebe
que los CSV no fueron alterados desde que el equipo los firmó.

Qué se comprueba
  1. La clave pública que recibes tiene la huella SHA3-256 oficial que tú indicas.
  2. La firma ML-DSA-65 (FIPS 204, contexto "manifiesto-datos") del manifiesto es válida
     con esa clave.
  3. Cada CSV de la carpeta coincide en tamaño y SHA3-256 con el manifiesto, y no sobra
     ni falta ningún CSV.

Ejemplos
  python verificar_manifiesto.py \\
      --manifiesto https://<dominio>/api/seguridad/manifiesto \\
      --clave-publica https://<dominio>/api/seguridad/clave-publica \\
      --huella <HUELLA_OFICIAL_PUBLICADA_EN_EL_README> --datos ./data

  python verificar_manifiesto.py --manifiesto data/manifest.json \\
      --clave-publica clave_publica.b64 --huella <HUELLA> --datos data

IMPORTANTE: la huella debe venir de un canal distinto al servidor que sirve la clave
(README del repositorio, landing). Si la tomas del mismo servidor, un atacante que lo
controle te serviría su propia clave. Códigos de salida: 0 correcto, 1 verificación fallida,
2 uso o entrada incorrectos.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PublicKey
except ImportError:  # librería ausente o anterior a la 47
    print('Falta `cryptography` >= 47 con ML-DSA: pip install "cryptography>=47"', file=sys.stderr)
    sys.exit(2)

# --- constantes del formato (deben coincidir con app/security/pqc.py) ---
ALGORITMO = "ML-DSA-65"
MANIFEST_VERSION = 1
CONTEXTO = b"manifiesto-datos"
CAMPO_FIRMA = "firma_b64"
PUBLIC_KEY_BYTES = 1952
SIGNATURE_BYTES = 3309
HTTP_TIMEOUT_S = 15
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
URL_SCHEMES = ("http://", "https://")
EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


class EntradaInvalida(Exception):
    """Entrada ilegible o mal formada (código de salida 2)."""


def _leer(origen: str) -> bytes:
    if origen.lower().startswith(URL_SCHEMES):
        with urllib.request.urlopen(origen, timeout=HTTP_TIMEOUT_S) as r:  # noqa: S310
            datos = r.read(MAX_DOWNLOAD_BYTES + 1)
    else:
        datos = Path(origen).read_bytes()
    if len(datos) > MAX_DOWNLOAD_BYTES:
        raise EntradaInvalida(f"{origen}: demasiado grande (máx. {MAX_DOWNLOAD_BYTES} bytes)")
    return datos


def _es_archivo(valor: str) -> bool:
    try:
        return Path(valor).is_file()
    except (OSError, ValueError):  # base64 largo: no es un nombre de archivo válido
        return False


def _clave_publica(origen: str) -> bytes:
    """Acepta: URL o archivo con JSON {"clave_publica_b64": ...}, archivo con base64, o base64 directo."""
    try:
        if origen.lower().startswith(URL_SCHEMES) or _es_archivo(origen):
            texto = _leer(origen).decode("utf-8").strip()
        else:
            texto = origen.strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise EntradaInvalida(f"No se pudo leer la clave pública: {exc}") from None
    if texto.startswith("{"):
        try:
            texto = json.loads(texto)["clave_publica_b64"]
        except (ValueError, KeyError, TypeError):
            raise EntradaInvalida('El JSON de la clave no trae "clave_publica_b64"') from None
    try:
        clave = base64.b64decode(texto, validate=True)
    except (binascii.Error, ValueError):
        raise EntradaInvalida("La clave pública no es base64 válido") from None
    if len(clave) != PUBLIC_KEY_BYTES:
        raise EntradaInvalida(f"La clave pública debe tener {PUBLIC_KEY_BYTES} bytes y tiene {len(clave)}")
    return clave


def _canonico(objeto) -> bytes:
    return json.dumps(objeto, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verificar(manifiesto: dict, clave: bytes, huella_esperada: str | None, datos: Path | None) -> list[str]:
    fallos: list[str] = []
    huella = hashlib.sha3_256(clave).hexdigest()
    if huella_esperada is not None and huella != huella_esperada.strip().lower():
        fallos.append(f"La huella de la clave ({huella}) NO coincide con la oficial indicada.")
    try:
        if manifiesto["version"] != MANIFEST_VERSION or manifiesto["algoritmo"] != ALGORITMO:
            return fallos + [f"Versión o algoritmo no soportados (se esperaba v{MANIFEST_VERSION}, {ALGORITMO})."]
        firma = base64.b64decode(manifiesto[CAMPO_FIRMA], validate=True)
        sin_firma = {k: v for k, v in manifiesto.items() if k != CAMPO_FIRMA}
        entradas = {e["nombre"]: e for e in manifiesto["archivos"]}
        if manifiesto["huella_clave_publica"] != huella:
            fallos.append("La huella declarada en el manifiesto no corresponde a la clave dada.")
        try:
            MLDSA65PublicKey.from_public_bytes(clave).verify(firma, _canonico(sin_firma), CONTEXTO)
        except InvalidSignature:
            fallos.append("La firma del manifiesto NO es válida: el manifiesto fue alterado o la clave no es la firmante.")
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        return fallos + [f"Manifiesto mal formado ({type(exc).__name__})."]
    if datos is not None:
        for nombre, e in sorted(entradas.items()):
            ruta = datos / nombre
            if Path(nombre).name != nombre or not ruta.is_file():
                fallos.append(f"{nombre}: figura en el manifiesto pero no existe en {datos}.")
                continue
            contenido = ruta.read_bytes()
            if len(contenido) != e.get("bytes"):
                fallos.append(f"{nombre}: tamaño distinto (manifiesto {e.get('bytes')}, real {len(contenido)}).")
            if hashlib.sha3_256(contenido).hexdigest() != e.get("sha3_256"):
                fallos.append(f"{nombre}: SHA3-256 distinto; el archivo fue modificado.")
        fallos += [f"{p.name}: está en {datos} pero no figura en el manifiesto."
                   for p in sorted(datos.glob("*.csv")) if p.is_file() and p.name not in entradas]
    return fallos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifiesto", required=True, help="ruta o URL de manifest.json")
    ap.add_argument("--clave-publica", required=True,
                    help="URL o archivo (JSON de la API o base64) o la clave en base64")
    ap.add_argument("--huella", help="huella SHA3-256 oficial de la clave (hex), de un canal independiente")
    ap.add_argument("--sin-huella", action="store_true",
                    help="no contrastar la huella (la verificación NO prueba quién firmó)")
    ap.add_argument("--datos", type=Path, help="carpeta con los CSV a comprobar")
    args = ap.parse_args(argv)
    if not args.huella and not args.sin_huella:
        print("Indica --huella <hex> (recomendado) o --sin-huella.", file=sys.stderr)
        return EXIT_USAGE
    try:
        manifiesto = json.loads(_leer(args.manifiesto).decode("utf-8"))
        clave = _clave_publica(args.clave_publica)
    except (EntradaInvalida, OSError, ValueError) as exc:
        print(f"Entrada incorrecta: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.datos is not None and not args.datos.is_dir():
        print(f"No existe la carpeta de datos: {args.datos}", file=sys.stderr)
        return EXIT_USAGE
    fallos = verificar(manifiesto, clave, args.huella, args.datos)
    if args.sin_huella:
        print("AVISO: sin contrastar la huella; esto solo prueba que firma y datos son coherentes entre sí.")
    for f in fallos:
        print(f"FALLO: {f}")
    if fallos:
        return EXIT_FAIL
    n = len(manifiesto["archivos"])
    comprobados = f"{n} archivos comprobados" if args.datos else "archivos NO comprobados (sin --datos)"
    print(f"CORRECTO: firma {ALGORITMO} válida; {comprobados}; huella {hashlib.sha3_256(clave).hexdigest()}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

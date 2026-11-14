"""Que .env.example no se quede atrás: toda variable de entorno que lee el código debe estar documentada."""
import re
from pathlib import Path

from app.core import config
from app.security import pqc

EJEMPLO = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
DOCUMENTADAS = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", EJEMPLO, flags=re.M))
FUERA_DEL_EJEMPLO = {"K_SERVICE"}          # la define Cloud Run, no se escribe a mano


def variables_del_codigo() -> set[str]:
    nombres = set()
    for modulo in (config, pqc):
        for atributo, valor in vars(modulo).items():
            if atributo.startswith("ENV_") and isinstance(valor, str):
                nombres.add(valor)
    return nombres | {"PORT"}


def test_toda_variable_del_codigo_esta_en_el_ejemplo():
    faltan = variables_del_codigo() - DOCUMENTADAS - FUERA_DEL_EJEMPLO
    assert not faltan, f"Faltan en .env.example: {sorted(faltan)}"


def test_el_ejemplo_no_contiene_secretos_reales():
    for linea in EJEMPLO.splitlines():
        if "=" in linea and not linea.lstrip().startswith("#"):
            nombre, valor = linea.split("=", 1)
            if re.search(r"KEY|SALT|PRIVATE|PASSWORD|SECRET", nombre):
                assert valor.strip() == "", f"{nombre} no debe llevar valor en el ejemplo"


def test_el_ejemplo_no_se_ignora_en_git():
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert ".env.example" not in gitignore.replace("!.env.example", "")

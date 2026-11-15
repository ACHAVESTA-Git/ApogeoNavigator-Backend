"""Forma estructural de un JSON: claves y tipos, sin valores. Sirve para congelar el contrato de la API."""
import json
from typing import Any


def forma(valor: Any) -> Any:
    """Estructura del valor: dict -> {clave: forma}, lista -> [forma unificada de sus elementos], escalares -> nombre de tipo.

    Los enteros y los decimales son ambos «number» (JSON no los distingue). Una lista vacía es «[]» y no aporta forma.
    """
    if valor is None:
        return "null"
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, (int, float)):
        return "number"
    if isinstance(valor, str):
        return "str"
    if isinstance(valor, list):
        resultado: Any = None
        for item in valor:
            resultado = forma(item) if resultado is None else unir(resultado, forma(item))
        return [] if resultado is None else [resultado]
    if isinstance(valor, dict):
        return {clave: forma(valor[clave]) for clave in sorted(valor)}
    raise TypeError(f"Tipo no JSON: {type(valor)}")


def unir(a: Any, b: Any) -> Any:
    """Unifica dos formas del mismo campo (p. ej. de dos elementos de una lista).

    Una clave que falta en alguno de los dos se marca con «?» (opcional); dos tipos distintos se unen con «|».
    """
    if a == b:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        salida = {}
        for clave in sorted(set(a) | set(b)):
            if clave in a and clave in b:
                salida[clave] = unir(a[clave], b[clave])
            else:
                salida[clave + "?"] = a.get(clave, b.get(clave))
        return salida
    if isinstance(a, list) and isinstance(b, list):
        if not a:
            return b
        if not b:
            return a
        return [unir(a[0], b[0])]
    if isinstance(a, str) and isinstance(b, str):
        return "|".join(sorted(set(a.split("|")) | set(b.split("|"))))
    return {"__o__": sorted([a, b], key=lambda x: json.dumps(x, sort_keys=True))}


def operaciones_openapi(esquema: dict) -> dict:
    """Operaciones de la API: ruta -> método -> parámetros (donde, nombre, obligatorio), cuerpo y códigos de respuesta."""
    salida: dict = {}
    for ruta, metodos in sorted(esquema["paths"].items()):
        for metodo, op in sorted(metodos.items()):
            parametros = sorted(f"{p['in']}:{p['name']}:{'obligatorio' if p.get('required') else 'opcional'}"
                                for p in op.get("parameters", []))
            cuerpo = sorted(op.get("requestBody", {}).get("content", {})) or None
            salida.setdefault(ruta, {})[metodo.upper()] = {
                "parametros": parametros, "cuerpo": cuerpo, "respuestas": sorted(op.get("responses", {})),
            }
    return salida

"""Catálogo único de mensajes de error: qué pasó, por qué y qué hacer. Nada de textos sueltos en los routers.

Cada respuesta de error lleva, junto a `detail` (que no cambia), un objeto `error` con código estable, título, mensaje y
acción. Hay un catálogo BASE por estado HTTP y códigos de DOMINIO, más específicos, que el manejador elige según el error.
"""
import re
from dataclasses import dataclass

from app.core import config as c


@dataclass(frozen=True)
class Mensaje:
    codigo: str
    estado: int
    titulo: str          # hasta 40 caracteres
    mensaje: str         # plantilla: {sitio}, {detalle}, {resumen}
    accion: str


def _m(codigo, estado, titulo, mensaje, accion) -> Mensaje:
    return Mensaje(codigo, estado, titulo, mensaje, accion)


# ---------------------------------------------------------------- catálogo base (por estado)
BASE: dict[int, Mensaje] = {m.estado: m for m in (
    _m("PETICION_INVALIDA", 400, "Petición inválida", "La petición no se pudo entender.", "Revisa los datos enviados."),
    _m("NO_AUTORIZADO", 401, "Falta autorización", "Esta operación necesita una clave válida.", "Introduce una clave válida (X-API-Key)."),
    _m("PROHIBIDO", 403, "Sin permiso", "Tu clave no permite esta operación.", "Usa una clave con permiso para esta acción."),
    _m("NO_ENCONTRADO", 404, "No encontrado", "No existe esa dirección o ese recurso.", "Revisa la dirección o el identificador."),
    _m("METODO_NO_PERMITIDO", 405, "Método no permitido", "Esa dirección no admite este método HTTP.", "Usa el método indicado en /docs."),
    _m("CONFLICTO", 409, "Conflicto", "{detalle}", "Los datos cambiaron mientras tanto; recarga y reintenta."),
    _m("ARCHIVO_DEMASIADO_GRANDE", 413, "Archivo demasiado grande", "El archivo supera el tamaño permitido.",
       "Máximo 5 MB y 10 000 filas; divide el archivo."),
    _m("TIPO_NO_SOPORTADO", 415, "Tipo de archivo no soportado", "El archivo no es un CSV de texto en UTF-8.", "Sube un archivo .csv en UTF-8."),
    _m("DATOS_INVALIDOS", 422, "Datos inválidos", "{detalle}", "Corrige los campos indicados y vuelve a intentarlo."),
    _m("DEMASIADAS_PETICIONES", 429, "Demasiadas peticiones", "Se han hecho demasiadas peticiones seguidas.", "Espera unos segundos y reintenta."),
    _m("ERROR_INTERNO", 500, "Error inesperado", "Ocurrió un error inesperado en el servidor.",
       "Reintenta; si persiste, comparte el código de seguimiento."),
    _m("SERVICIO_EXTERNO_FALLO", 502, "Fallo de un servicio externo", "Un servicio del que depende esta operación falló.",
       "Reintenta en unos minutos."),
    _m("SERVICIO_NO_DISPONIBLE", 503, "Servicio no disponible", "{detalle}",
       "Reintenta en unos minutos (datos, BD o almacenamiento caídos)."),
    _m("TIEMPO_AGOTADO", 504, "Tiempo de espera agotado", "La operación tardó demasiado.", "Reduce el rango de fechas o reintenta."),
)}

# ---------------------------------------------------------------- códigos de dominio (más específicos que su estado)
DOMINIO: dict[str, Mensaje] = {m.codigo: m for m in (
    _m("SITIO_NO_ENCONTRADO", 404, "Sitio no encontrado", "No hay ningún sitio con el identificador «{sitio}».",
       "Revisa el identificador o elige un sitio del catálogo (/api/sites)."),
    _m("EFEMERIDES_NO_ENCONTRADAS", 404, "Sin efemérides", "El sitio «{sitio}» no tiene efemérides cargadas.",
       "Elige otro sitio o carga sus efemérides."),
    _m("MANIFIESTO_NO_GENERADO", 404, "Aún no hay manifiesto", "Todavía no se ha firmado ningún manifiesto de los datos.",
       "Se genera con la primera carga válida; vuelve a intentarlo después."),
    _m("TIPO_DATOS_DESCONOCIDO", 404, "Tipo de datos desconocido", "{detalle}", "Usa sites, efemerides o metricas."),
    _m("USUARIO_NO_ENCONTRADO", 404, "Usuario no encontrado", "No existe ese usuario.", "Revisa el identificador del usuario."),
    _m("CLAVE_NO_ENCONTRADA", 404, "Clave no encontrada", "No existe esa clave.", "Revisa el identificador de la clave."),
    _m("CLAVE_INVALIDA", 401, "Clave inválida o ausente", "La clave de API no es válida o no se envió.",
       "Envía una clave válida en la cabecera X-API-Key."),
    _m("RANGO_FECHAS_INVALIDO", 422, "Rango de fechas inválido", "{detalle}",
       "Usa fechas UTC con el formato AAAA-MM-DDTHH:MMZ y que «hasta» sea posterior a «desde»."),
    _m("VARIABLE_INVALIDA", 422, "Variable no válida", "{detalle}",
       "Elige sol_el_deg, sol_az_deg, tierra_az_deg o tierra_el_deg."),
    _m("RANGO_DEMASIADO_LARGO", 422, "Rango demasiado largo", "{detalle}", "Pide un rango de 35 días como máximo y consulta por tramos."),
    _m("CSV_INVALIDO", 422, "CSV inválido", "{resumen}", "Corrige las filas indicadas en el informe (detail) y vuelve a subirlo."),
    _m("DATOS_NO_DISPONIBLES", 503, "Datos no disponibles", "No se pueden leer los datos en este momento.",
       "Reintenta en unos minutos; si persiste, avisa al equipo con el código de seguimiento."),
    _m("BD_NO_DISPONIBLE", 503, "Base de datos no disponible", "La base de datos no responde o no está configurada.",
       "Reintenta en unos minutos; si persiste, avisa al equipo con el código de seguimiento."),
    _m("ALMACEN_NO_DISPONIBLE", 503, "Almacenamiento no disponible", "No se pudo acceder al almacenamiento de datos; no se guardó nada.",
       "Reintenta en unos minutos."),
    _m("FIRMA_NO_DISPONIBLE", 503, "Firma no disponible", "La firma post-cuántica no está disponible; no se guardó nada.",
       "Avisa al equipo con el código de seguimiento: faltan las claves de firma."),
    _m("CLAVE_DUPLICADA", 409, "Clave ya activa", "Ya hay una clave activa para ese usuario y tipo.", "Revócala o rótala en lugar de generar otra."),
    _m("CLAVE_YA_REVOCADA", 409, "Clave ya revocada", "La clave ya está revocada.", "No hace falta revocarla de nuevo; rota o genera una nueva."),
    _m("USUARIO_INACTIVO", 409, "Usuario desactivado", "El usuario está desactivado; no se le pueden emitir claves.", "Activa al usuario antes de emitirle claves."),
)}

CODIGOS = {**{m.codigo: m for m in BASE.values()}, **DOMINIO}

# ---------------------------------------------------------------- de un texto de `detail` a su código de dominio
_POR_DETALLE: dict[str, str] = {
    c.MSG_SITE_NOT_IN_CATALOG: "SITIO_NO_ENCONTRADO", c.MSG_NO_EPHEMERIS: "EFEMERIDES_NO_ENCONTRADAS",
    c.MSG_NO_MANIFEST: "MANIFIESTO_NO_GENERADO", c.MSG_API_KEY_INVALID: "CLAVE_INVALIDA", c.MSG_KEYS_ADMIN_KEY: "CLAVE_INVALIDA",
    c.MSG_RANGE_ORDER: "RANGO_FECHAS_INVALIDO", c.MSG_AUDIT_RANGE_ORDER: "RANGO_FECHAS_INVALIDO",
    c.MSG_NO_SITES: "DATOS_NO_DISPONIBLES", c.MSG_SITES_INVALID: "DATOS_NO_DISPONIBLES", c.MSG_EPHEM_INVALID: "DATOS_NO_DISPONIBLES",
    c.MSG_METRICS_INVALID: "DATOS_NO_DISPONIBLES", c.MSG_DB_UNAVAILABLE: "BD_NO_DISPONIBLE", c.MSG_DB_DISABLED: "BD_NO_DISPONIBLE",
    c.MSG_BUCKET_UNAVAILABLE: "ALMACEN_NO_DISPONIBLE", c.MSG_SIGNING_UNAVAILABLE: "FIRMA_NO_DISPONIBLE",
    c.MSG_KEYS_NO_USER: "USUARIO_NO_ENCONTRADO", c.MSG_KEYS_NO_KEY: "CLAVE_NO_ENCONTRADA",
    c.MSG_KEYS_ALREADY_REVOKED: "CLAVE_YA_REVOCADA", c.MSG_KEYS_INACTIVE_USER: "USUARIO_INACTIVO",
    c.MSG_KEYS_ALREADY_ACTIVE_GENERIC: "CLAVE_DUPLICADA",
}
_POR_PATRON: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^'(desde|hasta)': "), "RANGO_FECHAS_INVALIDO"),
    (re.compile(r"está fuera del rango de datos disponible$"), "RANGO_FECHAS_INVALIDO"),
    (re.compile(r"^'(desde|hasta)' (es anterior|supera)"), "RANGO_FECHAS_INVALIDO"),
    (re.compile(r"^'variable' debe ser"), "VARIABLE_INVALIDA"),
    (re.compile(r"^El rango máximo es de \d+"), "RANGO_DEMASIADO_LARGO"),
    (re.compile(r"^El usuario ya tiene una clave activa"), "CLAVE_DUPLICADA"),
    (re.compile(r"^Tipo desconocido; usa uno de"), "TIPO_DATOS_DESCONOCIDO"),
]


def codigo_para(estado: int, detail=None, codigo: str | None = None) -> str:
    """Código del catálogo para un error: el pedido explícitamente, el de dominio que case con el texto, o el base del estado."""
    if codigo and codigo in CODIGOS and CODIGOS[codigo].estado == estado:
        return codigo
    if isinstance(detail, str):
        candidato = _POR_DETALLE.get(detail) or next((cod for patron, cod in _POR_PATRON if patron.search(detail)), None)
        if candidato and CODIGOS[candidato].estado == estado:
            return candidato
    return base_para(estado).codigo


def base_para(estado: int) -> Mensaje:
    """Entrada base del estado; los estados fuera del catálogo caen en la de su familia (4xx -> 400, 5xx -> 500)."""
    if estado in BASE:
        return BASE[estado]
    return BASE[400] if 400 <= estado < 500 else BASE[500]


def construir(codigo: str, contexto: dict | None = None) -> dict:
    """Parte fija del objeto `error` (sin request_id ni ts_utc). Variables que falten en el contexto no rompen el mensaje."""
    entrada = CODIGOS[codigo]
    datos = {"sitio": c.CONTEXT_MISSING, "detalle": "", "resumen": "", **(contexto or {})}
    mensaje = entrada.mensaje.format(**datos).strip() or base_para(entrada.estado).mensaje.split("{")[0].strip() or entrada.titulo
    return {"codigo": entrada.codigo, "estado": entrada.estado, "titulo": entrada.titulo, "mensaje": mensaje, "accion": entrada.accion}

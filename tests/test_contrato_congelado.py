"""Contrato congelado de la API: lo que consume el frontend no puede cambiar sin querer.

Cada escenario hace una petición real a la aplicación (datos reales de data/, base de datos SQLite, claves de prueba) y
compara con `tests/contrato/snapshots.json`: código HTTP, tipo de contenido, cabeceras relevantes, la FORMA del JSON
(claves y tipos, sin valores) y, en los errores con `detail` de texto, el texto exacto. Además se congelan las
operaciones de OpenAPI (rutas, métodos, parámetros y códigos de respuesta).

El refactor a DTO, repositorios y manejadores de error debe dejar estas pruebas en verde SIN tocar el snapshot.
Si un cambio de contrato es deliberado (y el frontend ya está avisado), regenera el snapshot con
    UPDATE_CONTRATO=1 pytest tests/test_contrato_congelado.py
y revisa el diff de `tests/contrato/snapshots.json` en el PR.
Fuente de verdad del lado del consumidor: ApogeoNavigator-Frontend/src/lib/tipos.ts y constants.ts.
"""
import json
import os
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.db import session as db_session
from app.db.enums import RolUsuario
from app.main import app
from app.security import gestor_claves as gk
from app.security import pqc
from app.services.usuarios import crear_usuario
from app.utils import auditoria_util
from tests.conftest import API_KEY, fixture_bytes
from tests.contrato.forma import forma, operaciones_openapi

SNAPSHOT = Path(__file__).parent / "contrato" / "snapshots.json"
REAL_DATA = Path(__file__).resolve().parents[1] / "data"
ADMIN_KEY = "clave-admin-de-prueba"
H_UPLOAD = {"X-API-Key": API_KEY}
H_ADMIN = {"X-API-Key": ADMIN_KEY}
DIC = {"desde": "2026-12-01T00:00Z", "hasta": "2027-01-01T00:00Z"}
CABECERAS_CONGELADAS = ("cache-control", "x-content-type-options", "referrer-policy", "x-frame-options")   # HSTS no: solo en producción


@pytest.fixture
def ctx(tmp_path, monkeypatch, pqc_keys, bd):
    datos = tmp_path / "data"
    datos.mkdir()
    for archivo in REAL_DATA.glob("*.csv"):
        shutil.copy(archivo, datos / archivo.name)
    for var in ("K_SERVICE", "ENVIRONMENT", "AUDIT_REQUIRE_SIGNATURE"):
        monkeypatch.delenv(var, raising=False)
    semilla, clave_hash = gk.generar_claves_servidor()
    for nombre, valor in (("DATA_DIR", str(datos)), ("UPLOAD_API_KEY", API_KEY), ("ADMIN_API_KEY", ADMIN_KEY),
                          ("IP_HASH_SALT", "sal-de-prueba"), ("PQC_PRIVATE_KEY", pqc_keys[0]), ("PQC_PUBLIC_KEY", pqc_keys[1]),
                          (config.ENV_KEM_PRIVATE, semilla), (config.ENV_EMAIL_HASH_KEY, clave_hash)):
        monkeypatch.setenv(nombre, valor)
    pqc.reiniciar()
    gk.reiniciar()
    auditoria_util.set_firmante()
    yield SimpleNamespace(client=TestClient(app, raise_server_exceptions=False), datos=datos)
    pqc.reiniciar()
    gk.reiniciar()


def _usuario() -> uuid.UUID:
    with db_session.session_scope() as s:
        u = crear_usuario(s, "ana@example.org", "Ana Ruiz", rol=RolUsuario.ANALISTA)
        s.flush()
        return u.id


def _subida(ctx, ruta, contenido, nombre, headers=H_UPLOAD):
    return ctx.client.post(ruta, headers=headers, files={"file": (nombre, contenido, "text/csv")})


def _claves_generadas(ctx):
    return ctx.client.post("/api/admin/claves", headers=H_ADMIN, json={"usuario_id": str(_usuario())})


def _con_clave(ctx, tipo="firma"):
    entrega = ctx.client.post("/api/admin/claves", headers=H_ADMIN,
                              json={"usuario_id": str(_usuario()), "tipos": [tipo]}).json()
    return entrega["claves"][0]["id"]


def _efemerides_cortas(sitio: str, dias: int = 90) -> bytes:
    lineas = (REAL_DATA / f"efemerides_{sitio}.csv").read_text(encoding="utf-8").splitlines()
    return ("\n".join(lineas[: 1 + 24 * dias]) + "\n").encode()


def _con_evento(ctx, peticion):
    _subida(ctx, "/api/datos/validar/sites", fixture_bytes("sites_valido.csv"), "sites.csv")      # deja un evento auditado
    return peticion(ctx)


def _horizons(modo="ok", **params):
    """/api/horizons con Horizons simulado (sin red) y sin esperas entre reintentos."""
    def peticion(ctx):
        from app.services import horizons
        from tests.horizons_falso import FakeHorizons
        antes = config.HORIZONS_RETRY_BACKOFF_S
        config.HORIZONS_RETRY_BACKOFF_S = 0
        horizons.set_transport(FakeHorizons(mode=modo).transport)
        horizons.reset_state()
        try:
            return ctx.client.get("/api/horizons", params={"lat": -89.66, "lon": 129.2, "desde": "2026-12-01T00:00Z",
                                                           "hasta": "2026-12-01T03:00Z", **params})
        finally:
            config.HORIZONS_RETRY_BACKOFF_S = antes
            horizons.set_transport(None)
            horizons.reset_state()
    return peticion


ESCENARIOS = {
    # ---- lectura (lo que consume el frontend)
    "health": lambda c: c.client.get("/api/health"),
    "sites": lambda c: c.client.get("/api/sites"),
    "efemerides_ok": lambda c: c.client.get("/api/efemerides/shackleton", params={"desde": "2026-12-01T00:00Z", "hasta": "2026-12-01T03:00Z"}),
    "efemerides_sin_rango": lambda c: c.client.get("/api/efemerides/shackleton"),
    "efemerides_sitio_inexistente_404": lambda c: c.client.get("/api/efemerides/no_existe"),
    "efemerides_fecha_invalida_422": lambda c: c.client.get("/api/efemerides/shackleton", params={"desde": "hola"}),
    "efemerides_rango_invertido_422": lambda c: c.client.get("/api/efemerides/shackleton", params={"desde": "2026-12-02T00:00Z", "hasta": "2026-12-01T00:00Z"}),
    "efemerides_rango_largo_422": lambda c: c.client.get("/api/efemerides/shackleton", params={"desde": "2026-12-01T00:00Z", "hasta": "2027-03-01T00:00Z"}),
    "metricas_un_sitio": lambda c: c.client.get("/api/metricas", params={"site_id": ["shackleton"], **DIC}),
    "metricas_tres_sitios": lambda c: c.client.get("/api/metricas", params={"site_id": ["shackleton", "change4", "apollo11"], **DIC}),
    "metricas_sin_referencia": lambda c: c.client.get("/api/metricas", params={"site_id": ["shackleton"], "desde": "2026-12-01T00:30Z", "hasta": "2026-12-08T00:30Z"}),
    "metricas_sin_site_id_422": lambda c: c.client.get("/api/metricas"),
    "metricas_cuatro_sitios_422": lambda c: c.client.get("/api/metricas", params={"site_id": ["apollo11", "apollo15", "apollo17", "slim"], **DIC}),
    "metricas_sitio_inexistente_404": lambda c: c.client.get("/api/metricas", params={"site_id": ["no_existe"], **DIC}),
    "metricas_sin_sol_media_nula": lambda c: c.client.get("/api/metricas", params={"site_id": ["shackleton"], "desde": "2027-04-01T00:00Z", "hasta": "2027-05-01T00:00Z"}),
    "puestas_tierra_nunca_visible_con_nulos": lambda c: c.client.get("/api/puestas/change4", params={"desde": "2026-12-01T00:00Z"}),
    "sites_con_notas_nulas_y_altitud_por_defecto": lambda c: (c.datos.joinpath("sites.csv").write_bytes(fixture_bytes("sites_valido.csv")),
                                                              c.client.get("/api/sites"))[1],
    "puestas_ok": lambda c: c.client.get("/api/puestas/shackleton", params={"desde": "2026-12-01T00:00Z"}),
    "puestas_sin_desde": lambda c: c.client.get("/api/puestas/apollo11"),
    "puestas_sitio_inexistente_404": lambda c: c.client.get("/api/puestas/no_existe"),
    "ruta_inexistente_404": lambda c: c.client.get("/api/no-existe"),
    # ---- simulación del cielo (datos reales y proyección)
    "simulacion_datos": lambda c: c.client.get("/api/simulacion/shackleton", params={"desde": "2026-12-01T00:00Z", "hasta": "2026-12-01T03:00Z"}),
    "simulacion_con_proyeccion": lambda c: c.client.get("/api/simulacion/shackleton", params={"desde": "2027-05-31T00:00Z", "hasta": "2027-06-02T00:00Z", "paso_horas": 12}),
    "simulacion_paso_invalido_422": lambda c: c.client.get("/api/simulacion/shackleton", params={"paso_horas": 0.01}),
    "simulacion_sitio_inexistente_404": lambda c: c.client.get("/api/simulacion/no_existe"),
    # ---- transparencia del análisis
    "analisis_ok": lambda c: c.client.get("/api/analisis/shackleton"),
    "analisis_otra_variable_y_rango": lambda c: c.client.get("/api/analisis/apollo15", params={"variable": "tierra_az_deg", "desde": "2027-01-01T00:00Z", "hasta": "2027-07-01T00:00Z"}),
    "analisis_tras_subir_efemerides": lambda c: (_subida(c, "/api/datos/efemerides", _efemerides_cortas("slim"), "efemerides_slim.csv"),
                                                 c.client.get("/api/analisis/slim"))[1],
    "analisis_sitio_inexistente_404": lambda c: c.client.get("/api/analisis/no_existe"),
    "analisis_variable_invalida_422": lambda c: c.client.get("/api/analisis/shackleton", params={"variable": "temperatura"}),
    "analisis_rango_invalido_422": lambda c: c.client.get("/api/analisis/shackleton", params={"desde": "2027-01-01T00:00Z", "hasta": "2026-12-01T00:00Z"}),
    "metodo_no_permitido_405": lambda c: c.client.post("/api/sites"),
    # ---- coordenadas propias (Horizons en vivo, simulado)
    "horizons_ok": _horizons(),
    "horizons_lat_fuera_de_rango_422": _horizons(lat=91),
    "horizons_rango_largo_422": _horizons(hasta="2027-03-01T00:00Z"),
    "horizons_jpl_falla_502": _horizons("http500"),
    "horizons_jpl_tarda_504": _horizons("timeout"),
    # ---- seguridad (público)
    "seguridad_clave_publica": lambda c: c.client.get("/api/seguridad/clave-publica"),
    "seguridad_manifiesto_sin_generar_404": lambda c: c.client.get("/api/seguridad/manifiesto"),
    "seguridad_manifiesto": lambda c: (_subida(c, "/api/datos/sites", (REAL_DATA / "sites.csv").read_bytes(), "sites.csv"),
                                       c.client.get("/api/seguridad/manifiesto"))[1],
    # ---- carga de CSV
    "datos_validar_ok": lambda c: _subida(c, "/api/datos/validar/sites", fixture_bytes("sites_valido.csv"), "sites.csv"),
    "datos_validar_con_advertencias": lambda c: _subida(c, "/api/datos/validar/sites", fixture_bytes("sites_advertencias.csv"), "sites.csv"),
    "datos_validar_invalido_422": lambda c: _subida(c, "/api/datos/validar/sites", fixture_bytes("rango.csv"), "sites.csv"),
    "datos_guardar_ok": lambda c: _subida(c, "/api/datos/sites", (REAL_DATA / "sites.csv").read_bytes(), "sites.csv"),
    "datos_guardar_con_historial": lambda c: (_subida(c, "/api/datos/sites", (REAL_DATA / "sites.csv").read_bytes(), "sites.csv"),
                                              _subida(c, "/api/datos/sites", (REAL_DATA / "sites.csv").read_bytes(), "sites.csv"))[1],
    "datos_guardar_invalido_422": lambda c: _subida(c, "/api/datos/sites", fixture_bytes("rango.csv"), "sites.csv"),
    "datos_sin_clave_401": lambda c: c.client.post("/api/datos/validar/sites", files={"file": ("sites.csv", b"x")}),
    "datos_clave_incorrecta_401": lambda c: _subida(c, "/api/datos/validar/sites", b"x", "sites.csv", headers={"X-API-Key": "mala"}),
    "datos_tipo_desconocido_404": lambda c: _subida(c, "/api/datos/validar/otro", b"x", "sites.csv"),
    "datos_extension_415": lambda c: _subida(c, "/api/datos/validar/sites", b"x", "sites.txt"),
    "datos_demasiado_grande_413": lambda c: _subida(c, "/api/datos/validar/sites", b"a" * (config.MAX_FILE_BYTES + config.MULTIPART_OVERHEAD_BYTES + 10), "sites.csv"),
    "datos_sin_archivo_422": lambda c: c.client.post("/api/datos/validar/sites", headers=H_UPLOAD),
    # ---- auditoría
    "auditoria_listar": lambda c: _con_evento(c, lambda c: c.client.get("/api/auditoria", headers=H_UPLOAD)),
    "auditoria_listar_con_firma": lambda c: _con_evento(c, lambda c: c.client.get("/api/auditoria", params={"incluir_firma": True}, headers=H_UPLOAD)),
    "auditoria_verificar": lambda c: _con_evento(c, lambda c: c.client.get("/api/auditoria/verificar", headers=H_UPLOAD)),
    "auditoria_sin_clave_401": lambda c: c.client.get("/api/auditoria"),
    "auditoria_parametro_invalido_422": lambda c: c.client.get("/api/auditoria", params={"limite": 0}, headers=H_UPLOAD),
    "auditoria_sin_base_de_datos_503": lambda c: (db_session.configure(None), c.client.get("/api/auditoria", headers=H_UPLOAD))[1],
    # ---- gestor de claves
    "claves_listar_vacio": lambda c: c.client.get("/api/admin/claves", headers=H_ADMIN),
    "claves_generar_201": _claves_generadas,
    "claves_listar_con_datos": lambda c: (_claves_generadas(c), c.client.get("/api/admin/claves", headers=H_ADMIN))[1],
    "claves_sin_clave_401": lambda c: c.client.get("/api/admin/claves"),
    "claves_generar_usuario_inexistente": lambda c: c.client.post("/api/admin/claves", headers=H_ADMIN, json={"usuario_id": str(uuid.uuid4())}),
    "claves_generar_cuerpo_invalido_422": lambda c: c.client.post("/api/admin/claves", headers=H_ADMIN, json={}),
    "claves_publica": lambda c: c.client.get(f"/api/admin/claves/{_con_clave(c)}/publica", headers=H_ADMIN),
    "claves_huella": lambda c: c.client.get(f"/api/admin/claves/{_con_clave(c)}/huella", headers=H_ADMIN),
    "claves_revocar": lambda c: c.client.post(f"/api/admin/claves/{_con_clave(c)}/revocar", headers=H_ADMIN, json={"motivo": "Prueba de contrato"}),
    "claves_rotar_201": lambda c: c.client.post(f"/api/admin/claves/{_con_clave(c)}/rotar", headers=H_ADMIN, json={"motivo": "Rotación de prueba"}),
    "claves_inexistente_404": lambda c: c.client.get(f"/api/admin/claves/{uuid.uuid4()}/huella", headers=H_ADMIN),
}


def observar(respuesta) -> dict:
    """Lo que se congela de una respuesta."""
    tipo = respuesta.headers.get("content-type", "").split(";")[0]
    observado = {"estado": respuesta.status_code, "tipo_contenido": tipo}
    cabeceras = {k: respuesta.headers[k] for k in CABECERAS_CONGELADAS if k in respuesta.headers}
    if "x-request-id" in respuesta.headers:          # el valor cambia en cada petición: se congela solo que esté
        cabeceras["x-request-id"] = "presente"
    if cabeceras:
        observado["cabeceras"] = cabeceras
    if tipo == "application/json":
        cuerpo = respuesta.json()
        observado["forma"] = forma(cuerpo)
        if isinstance(cuerpo, dict) and isinstance(cuerpo.get("detail"), str):
            observado["detail"] = cuerpo["detail"]
    return observado


def _cargar() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}


def _guardar(datos: dict) -> None:
    SNAPSHOT.write_text(json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


ACTUALIZAR = os.getenv("UPDATE_CONTRATO") == "1"


@pytest.mark.parametrize("nombre", sorted(ESCENARIOS))
def test_contrato_congelado(ctx, nombre):
    observado = observar(ESCENARIOS[nombre](ctx))
    snapshot = _cargar()
    if ACTUALIZAR:
        snapshot[nombre] = observado
        _guardar(snapshot)
        return
    assert nombre in snapshot, f"Falta «{nombre}» en el snapshot: genera con UPDATE_CONTRATO=1"
    assert observado == snapshot[nombre], f"El contrato de «{nombre}» cambió"


def test_operaciones_openapi_congeladas():
    actual = operaciones_openapi(app.openapi())
    snapshot = _cargar()
    if ACTUALIZAR:
        snapshot["__openapi__"] = actual
        _guardar(snapshot)
        return
    assert actual == snapshot["__openapi__"], "Cambió una ruta, un método, un parámetro o un código de respuesta"


def test_el_snapshot_no_tiene_escenarios_huerfanos():
    if ACTUALIZAR:
        pytest.skip("regenerando")
    assert set(_cargar()) - {"__openapi__"} == set(ESCENARIOS)


# Campos que declara el frontend en ApogeoNavigator-Frontend/src/lib/tipos.ts (copiados a mano: si el frontend cambia
# sus tipos, hay que actualizar esta tabla y el snapshot a la vez). Son las claves exactas de cada objeto de la API.
CAMPOS_FRONTEND = {
    ("sites", ("forma", 0)): {"site_id", "nombre", "categoria", "mision", "agencia", "lat_deg", "lon_este_deg", "alt_km", "fuente_url", "notas"},
    ("efemerides_ok", ("forma",)): {"site_id", "inicio_utc", "fin_utc", "paso_horas", "filas"},
    ("efemerides_ok", ("forma", "filas", 0)): {"utc", "sol_az_deg", "sol_el_deg", "tierra_az_deg", "tierra_el_deg"},
    ("metricas_un_sitio", ("forma", "resultados", 0)): {"site_id", "inicio_utc", "fin_utc", "horas_sol", "pct_sol", "sol_el_max_deg",
                                                      "sol_el_media_deg", "horas_tierra_visible", "pct_tierra", "semaforo", "avisos",
                                                      "origen", "referencia"},
    ("metricas_un_sitio", ("forma", "resultados", 0, "semaforo")): {"energia", "comunicacion", "global"},
    ("metricas_un_sitio", ("forma", "resultados", 0, "referencia")): {"horas_sol", "pct_sol", "sol_el_max_deg", "sol_el_media_deg",
                                                                    "horas_tierra_visible", "pct_tierra", "coincide"},
    ("puestas_ok", ("forma",)): {"site_id", "desde_utc", "sol", "tierra"},
    ("puestas_ok", ("forma", "sol")): {"visible", "proxima_puesta_utc", "proxima_salida_utc", "horas_restantes"},
    ("datos_validar_ok", ("forma",)): {"archivo", "tipo", "valido", "total_filas", "total_errores", "errores", "advertencias"},
    ("datos_guardar_ok", ("forma",)): {"archivo", "tipo", "valido", "total_filas", "total_errores", "errores", "advertencias",
                                       "guardado", "copia_historial"},
    ("datos_validar_invalido_422", ("forma", "detail", "errores", 0)): {"fila", "columna", "codigo", "mensaje", "valor"},
    # Transparencia del análisis (sección nueva del frontend): claves exactas del DTO que se le entregó a Fable.
    ("analisis_ok", ("forma",)): {"site_id", "desde_utc", "hasta_utc", "pasos", "entrada", "espectro", "modelo", "validacion",
                                  "limitaciones", "serie"},
    ("analisis_ok", ("forma", "pasos", 0)): {"id", "nombre", "descripcion", "estado", "resultado"},
    ("analisis_ok", ("forma", "pasos", 0, "resultado", 0)): {"nombre", "valor", "unidad"},
    ("analisis_ok", ("forma", "entrada")): {"archivo", "filas", "bytes", "inicio_utc", "fin_utc", "paso_horas", "calidad", "origen"},
    ("analisis_ok", ("forma", "entrada", "calidad")): {"filas_validas", "filas_con_error", "huecos", "paso_constante"},
    ("analisis_ok", ("forma", "entrada", "origen")): {"tipo", "descripcion", "sha3_256", "coincide_con_semilla", "ultima_carga", "manifiesto"},
    ("analisis_tras_subir_efemerides", ("forma", "entrada", "origen", "ultima_carga")): {"cargado_utc", "archivo", "sha3_256", "coincide",
                                                                                        "cargado_por"},
    ("analisis_tras_subir_efemerides", ("forma", "entrada", "origen", "manifiesto")): {"sha3_256", "generado_utc", "firmado", "firma_nota", "coincide"},
    ("analisis_ok", ("forma", "espectro")): {"ventana_dias", "resolucion_dias", "sinodico_teorico_dias", "sinodico_afinado_dias",
                                             "series", "nota"},
    ("analisis_ok", ("forma", "espectro", "series", 0)): {"variable", "nombre", "picos"},
    ("analisis_ok", ("forma", "espectro", "series", 0, "picos", 0)): {"periodo_dias", "amplitud_rel", "candidatos", "es_sinodico"},
    ("analisis_ok", ("forma", "modelo")): {"terminos_por_cuerpo", "periodos", "ajuste_directo_elevacion_sol", "r2", "residuos",
                                          "nota_ajuste"},
    ("analisis_ok", ("forma", "modelo", "periodos", 0)): {"cuerpo", "periodo_dias", "rol"},
    ("analisis_ok", ("forma", "validacion")): {"disponible", "vigente", "motivo", "validacion_desde_utc", "validacion_hasta_utc",
                                               "filas_entrenamiento_validadas", "rmse_deg", "mae_deg", "rmse_referencia_ingenua_deg",
                                               "mejora_frente_a_ingenua", "rmse_por_mes_deg", "semaforo_aciertos", "puestas_sol"},
    ("analisis_ok", ("forma", "limitaciones", 0)): {"codigo", "mensaje", "aplica"},
    ("analisis_ok", ("forma", "serie")): {"variable", "nombre", "paso_horas", "fin_datos_utc", "puntos"},
    ("analisis_ok", ("forma", "serie", "puntos", 0)): {"utc", "observado", "ajustado", "proyectado"},
    # Simulación del cielo.
    ("simulacion_con_proyeccion", ("forma",)): {"site_id", "desde_utc", "hasta_utc", "paso_horas", "fin_datos_utc", "avisos", "puntos"},
    ("simulacion_con_proyeccion", ("forma", "puntos", 0)): {"utc", "origen", "sol_az_deg", "sol_el_deg", "tierra_az_deg", "tierra_el_deg",
                                                            "azimut_solar_fiable", "incertidumbre_deg"},
}


def _bajar(nodo, ruta):
    for paso in ruta:
        nodo = nodo[0] if paso == 0 else nodo[paso]
    return nodo


@pytest.mark.parametrize("clave", sorted(CAMPOS_FRONTEND, key=str), ids=lambda k: f"{k[0]}:{'.'.join(map(str, k[1]))}")
def test_el_snapshot_tiene_exactamente_los_campos_que_declara_el_frontend(clave):
    nombre, ruta = clave
    objeto = _bajar(_cargar()[nombre], ruta)
    assert {k.rstrip("?") for k in objeto} == CAMPOS_FRONTEND[clave]


def test_los_campos_que_pueden_ser_nulos_lo_son_en_algun_escenario():
    """El frontend los declara `T | null`; el snapshot debe haber visto el nulo, o el contrato no los cubre."""
    snap = _cargar()
    assert snap["sites_con_notas_nulas_y_altitud_por_defecto"]["forma"][0]["notas"] == "null|str"
    assert snap["metricas_sin_sol_media_nula"]["forma"]["resultados"][0]["sol_el_media_deg"] == "null"
    tierra = snap["puestas_tierra_nunca_visible_con_nulos"]["forma"]["tierra"]
    assert tierra["proxima_puesta_utc"] == tierra["proxima_salida_utc"] == tierra["horas_restantes"] == "null"
    assert snap["metricas_un_sitio"]["forma"]["resultados"][0]["sol_el_media_deg"] == "number"
    assert snap["metricas_sin_referencia"]["forma"]["resultados"][0]["referencia"] == "null"
    assert snap["datos_guardar_ok"]["forma"]["copia_historial"] in ("null", "str")


def test_la_transparencia_distingue_nulos_y_valores_en_el_origen_y_la_serie():
    snap = _cargar()
    origen = snap["analisis_ok"]["forma"]["entrada"]["origen"]
    assert origen["ultima_carga"] == "null" and origen["manifiesto"] == "null"          # datos de la semilla: sin carga registrada
    tras = snap["analisis_tras_subir_efemerides"]["forma"]["entrada"]["origen"]
    assert tras["ultima_carga"] != "null" and tras["manifiesto"] != "null"
    punto = snap["analisis_ok"]["forma"]["serie"]["puntos"][0]
    assert {"null", "number"} <= set(punto["observado"].split("|")) | {"null"}
    assert snap["analisis_ok"]["forma"]["validacion"]["motivo"] == "null"

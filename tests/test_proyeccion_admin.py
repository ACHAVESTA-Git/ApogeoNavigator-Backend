"""GET /api/proyeccion: solo administradores con sesión, datos sintéticos (solo en tests), modelo tolerante y auditoría."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import config as c
from app.db.enums import AccionAuditoria, RolUsuario
from app.db.models import EventoAuditoria
# Se reutiliza el entorno de las pruebas de login: base SQLite, claves de prueba, directorio de datos temporal y cliente.
from tests.test_auth_login import ADMIN_KEY, SITES, UPLOAD_KEY, _entrar, _usuario, client, engine, entorno  # noqa: F401

RUTA = "/api/proyeccion"
T0 = datetime(2027, 6, 1, 1, tzinfo=timezone.utc)
HORAS = 24 * 61                                            # junio y julio completos, más el 1 de agosto casi entero


def _sol_arriba(i):          # 10 días con Sol, 10 sin Sol
    return (i // 240) % 2 == 0


def _tierra_arriba(i):       # 15 días con Tierra, 5 sin ella
    return (i // 120) % 4 != 3


def _csv(horas=HORAS, extra=True):
    cab = "utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg" + (",origen,modelo_version" if extra else "")
    lineas = [cab]
    for i in range(horas):
        utc = (T0 + timedelta(hours=i)).strftime(c.PROYECCION_UTC_FORMAT)
        fila = f"{utc},{(i * 1.5) % 360:.4f},{3.0 if _sol_arriba(i) else -3.0},{(i * 0.5) % 360:.4f},{4.0 if _tierra_arriba(i) else -4.0}"
        lineas.append(fila + (",proyeccion,sintetico-v0" if extra else ""))
    return "\n".join(lineas) + "\n"


MODELO = {
    "version": "sintetico-v0", "metodo": "armonico", "entrenado_desde": "2026-12-01T00:00Z", "entrenado_hasta": "2027-06-01T00:00Z",
    "proyectado_hasta": "2027-08-01T00:00Z",
    "error": {"rmse_deg": {"sol_el_deg": 0.1, "tierra_el_deg": 0.2},
              "por_horizonte": [{"meses": 1, "rmse_sol_el_deg": 0.1, "rmse_tierra_el_deg": 0.2}],
              "control_horizons": [{"site_id": "shackleton", "utc": "2027-07-01T00:00Z", "variable": "sol_el_deg", "error_deg": 0.05}],
              "aciertos_semaforo_control": {"aciertos": 5, "total": 6}},
}


@pytest.fixture
def datos(tmp_path, monkeypatch):
    carpeta = tmp_path / "data"
    (carpeta / "sites.csv").write_bytes(SITES)
    (carpeta / c.PROYECCION_DIR).mkdir()
    (carpeta / c.PROYECCION_DIR / "proyeccion_shackleton.csv").write_text(_csv(), encoding="utf-8")
    (carpeta / c.PROYECCION_DIR / c.PROYECCION_MODELO_FILENAME).write_text(json.dumps(MODELO), encoding="utf-8")
    return carpeta / c.PROYECCION_DIR


def _como(client, engine, rol):
    _usuario(engine, f"{rol.value}@apogeo.test", rol=rol)
    assert _entrar(client, f"{rol.value}@apogeo.test").status_code == 200


def _eventos(engine):
    with Session(engine) as s:
        return list(s.scalars(select(EventoAuditoria).where(EventoAuditoria.accion == AccionAuditoria.VER_PROYECCION)))


# ---------------------------------------------------------------- quién entra
def test_sin_sesion_es_401_no_autorizado(client, datos):
    r = client.get(RUTA, params={"site_id": "shackleton"})
    assert r.status_code == 401 and r.json()["error"]["codigo"] == "NO_AUTORIZADO"


@pytest.mark.parametrize("cabecera", [{c.API_KEY_HEADER: ADMIN_KEY}, {c.API_KEY_HEADER: UPLOAD_KEY}])
def test_la_api_key_no_da_acceso(client, datos, cabecera):
    r = client.get(RUTA, params={"site_id": "shackleton"}, headers=cabecera)
    assert r.status_code == 401 and r.json()["error"]["codigo"] == "NO_AUTORIZADO"


@pytest.mark.parametrize("rol", [RolUsuario.ANALISTA, RolUsuario.REVISOR])
def test_analista_y_revisor_reciben_403_y_no_se_audita(client, engine, datos, rol):
    _como(client, engine, rol)
    r = client.get(RUTA, params={"site_id": "shackleton"})
    assert r.status_code == 403 and r.json()["error"]["codigo"] == "ROL_INSUFICIENTE"
    assert "serie" not in r.text and _eventos(engine) == []


def test_admin_con_sesion_recibe_la_proyeccion(client, engine, datos):
    _como(client, engine, RolUsuario.ADMIN)
    r = client.get(RUTA, params={"site_id": "shackleton"})
    assert r.status_code == 200 and r.json()["origen"] == "proyeccion"


# ---------------------------------------------------------------- contenido
def test_serie_y_metricas_mensuales(client, engine, datos):
    _como(client, engine, RolUsuario.ADMIN)
    cuerpo = client.get(RUTA, params={"site_id": "shackleton"}).json()
    sitio = cuerpo["resultados"][0]
    assert len(sitio["serie"]) == -(-HORAS // 6)                       # paso por defecto: 6 h
    assert set(sitio["serie"][0]) == {"utc", "sol_az_deg", "sol_el_deg", "tierra_az_deg", "tierra_el_deg"}
    assert sitio["serie"][0]["utc"] == "2027-06-01T01:00Z"
    assert sitio["sha3_256"] == hashlib.sha3_256((datos / "proyeccion_shackleton.csv").read_bytes()).hexdigest()
    assert [m["mes"] for m in sitio["meses"]] == ["2027-06", "2027-07", "2027-08"]
    for mes in sitio["meses"]:
        assert set(mes) == {"mes", "pct_sol", "pct_tierra", "semaforo", "indicadores_astronauta"}
        assert set(mes["semaforo"]) == {"energia", "comunicacion"}
        assert "noche_mas_larga" in mes["indicadores_astronauta"]
    junio = [i for i in range(HORAS) if (T0 + timedelta(hours=i)).month == 6]
    esperado = round(sum(_sol_arriba(i) for i in junio) / len(junio) * 100, 2)
    assert sitio["meses"][0]["pct_sol"] == esperado
    assert sitio["meses"][0]["indicadores_astronauta"]["noche_mas_larga"]["horas"] == 240.0


@pytest.mark.parametrize("paso", [1, 24])
def test_paso_horas_submuestrea_la_serie_pero_no_las_metricas(client, engine, datos, paso):
    _como(client, engine, RolUsuario.ADMIN)
    base = client.get(RUTA, params={"site_id": "shackleton"}).json()["resultados"][0]["meses"]
    r = client.get(RUTA, params={"site_id": "shackleton", "paso_horas": str(paso)}).json()["resultados"][0]
    assert len(r["serie"]) == -(-HORAS // paso) and r["meses"] == base


def test_rango_pedido_recorta_serie_y_meses(client, engine, datos):
    _como(client, engine, RolUsuario.ADMIN)
    sitio = client.get(RUTA, params={"site_id": "shackleton", "desde": "2027-07-01T00:00Z", "hasta": "2027-07-10T00:00Z"}).json()["resultados"][0]
    assert [m["mes"] for m in sitio["meses"]] == ["2027-07"]
    assert sitio["serie"][0]["utc"] == "2027-07-01T00:00Z" and sitio["serie"][-1]["utc"] <= "2027-07-10T00:00Z"


def test_el_modelo_sale_del_archivo_y_lo_que_falta_es_null(client, engine, datos):
    _como(client, engine, RolUsuario.ADMIN)
    cuerpo = client.get(RUTA, params={"site_id": "shackleton"}).json()
    modelo = cuerpo["modelo"]
    assert modelo["version"] == "sintetico-v0" and modelo["error"]["rmse_deg"]["sol_el_deg"] == 0.1
    assert modelo["error"]["rmse_deg"]["sol_az_deg"] is None and modelo["error"]["rmse_deg"]["tierra_az_deg"] is None
    assert modelo["error"]["aciertos_semaforo_control"] == {"aciertos": 5, "total": 6}
    assert {lim["codigo"] for lim in cuerpo["limitaciones"]} == {c.LIM_NO_ES_EFEMERIDE, c.LIM_HORIZONTE_IDEAL}


def test_sin_archivo_de_modelo_todo_es_null_y_se_explica(client, engine, datos):
    (datos / c.PROYECCION_MODELO_FILENAME).unlink()
    _como(client, engine, RolUsuario.ADMIN)
    cuerpo = client.get(RUTA, params={"site_id": "shackleton"}).json()
    assert cuerpo["modelo"]["version"] is None and cuerpo["modelo"]["error"]["por_horizonte"] is None
    assert cuerpo["modelo"]["error"]["rmse_deg"] == {"sol_el_deg": None, "tierra_el_deg": None, "sol_az_deg": None, "tierra_az_deg": None}
    assert c.LIM_SIN_MODELO in {lim["codigo"] for lim in cuerpo["limitaciones"]}


def test_un_modelo_ilegible_no_rompe_la_respuesta(client, engine, datos):
    (datos / c.PROYECCION_MODELO_FILENAME).write_text("{no es json", encoding="utf-8")
    _como(client, engine, RolUsuario.ADMIN)
    r = client.get(RUTA, params={"site_id": "shackleton"})
    assert r.status_code == 200 and r.json()["modelo"]["version"] is None


def test_un_sitio_sin_proyeccion_va_vacio_con_la_limitacion_explicita(client, engine, datos):
    _como(client, engine, RolUsuario.ADMIN)
    cuerpo = client.get(RUTA, params={"site_id": ["shackleton", "apollo11"]}).json()
    assert cuerpo["origen"] == "proyeccion"
    assert cuerpo["resultados"][1] == {"site_id": "apollo11", "sha3_256": None, "serie": [], "meses": []}
    assert cuerpo["resultados"][0]["serie"]
    sin = next(lim for lim in cuerpo["limitaciones"] if lim["codigo"] == c.LIM_SIN_DATOS)
    assert "apollo11" in sin["mensaje"] and "shackleton" not in sin["mensaje"]


def test_las_columnas_extra_son_opcionales(client, engine, datos):
    (datos / "proyeccion_shackleton.csv").write_text(_csv(extra=False), encoding="utf-8")
    _como(client, engine, RolUsuario.ADMIN)
    assert client.get(RUTA, params={"site_id": "shackleton"}).status_code == 200


# ---------------------------------------------------------------- auditoría
def test_cada_consulta_queda_auditada_con_el_usuario_y_sin_datos_personales(client, engine, datos):
    uid = _usuario(engine, "jefa@apogeo.test", rol=RolUsuario.ADMIN, nombre="Jefa Secreta")
    assert _entrar(client, "jefa@apogeo.test").status_code == 200
    client.get(RUTA, params={"site_id": "shackleton", "paso_horas": "24"})
    eventos = _eventos(engine)
    assert len(eventos) == 1
    ev = eventos[0]
    assert ev.usuario_id == uid and ev.actor == c.ACTOR_USER and ev.recurso == c.MSG_PROJ_AUDIT_RESOURCE
    assert ev.detalle["sitios"] == ["shackleton"] and ev.detalle["paso_horas"] == 24
    assert "jefa" not in json.dumps(ev.detalle).lower() and "secreta" not in json.dumps(ev.detalle).lower()
    assert ev.hash_actual and ev.ts_utc is not None


# ---------------------------------------------------------------- errores
@pytest.mark.parametrize("params,estado,codigo", [
    ({"site_id": "shackleton", "paso_horas": "12"}, 422, "PASO_HORAS_INVALIDO"),
    ({"site_id": "shackleton", "paso_horas": "x"}, 422, "PASO_HORAS_INVALIDO"),
    ({"site_id": "shackleton", "desde": "2027-06-02T10:15Z", "hasta": "2027-06-02T10:45Z"}, 422, "RANGO_SIN_DATOS"),
    ({}, 422, "SITIO_REQUERIDO"),
    ({"site_id": "no_existe"}, 404, "SITIO_NO_ENCONTRADO"),
    ({"site_id": "shackleton", "desde": "2030-01-01T00:00Z"}, 422, "FECHA_FUERA_DE_DATOS"),
    ({"site_id": "shackleton", "desde": "2027-07-02T00:00Z", "hasta": "2027-07-01T00:00Z"}, 422, "RANGO_FECHAS_INVALIDO"),
    ({"site_id": ["apollo11", "apollo15", "apollo17", "shackleton", "slim", "change4"]}, 422, "COMPARACION_SITIOS_INVALIDA"),
])
def test_errores_con_el_estandar(client, engine, datos, params, estado, codigo):
    _como(client, engine, RolUsuario.ADMIN)
    r = client.get(RUTA, params=params)
    assert r.status_code == estado and r.json()["error"]["codigo"] == codigo


@pytest.mark.parametrize("contenido", ["utc,sol_az_deg\n2027-06-01T01:00Z,1\n", "", _csv(5).replace(",3.0,", ",abc,"),
                                       _csv(5).replace("2027-06-01T02:00Z", "2027-06-01T04:00Z")])
def test_un_csv_con_formato_roto_es_503_proyeccion_invalida(client, engine, datos, contenido):
    (datos / "proyeccion_shackleton.csv").write_text(contenido, encoding="utf-8")
    _como(client, engine, RolUsuario.ADMIN)
    r = client.get(RUTA, params={"site_id": "shackleton"})
    assert r.status_code == 503 and r.json()["error"]["codigo"] == "PROYECCION_INVALIDA"


def test_un_paso_menor_que_el_del_archivo_es_422_y_no_se_redondea(client, engine, datos):
    lineas = _csv(48).splitlines()
    sparse = [lineas[0]] + lineas[1::6]                       # el archivo pasa a 6 h
    (datos / "proyeccion_shackleton.csv").write_text("\n".join(sparse) + "\n", encoding="utf-8")
    _como(client, engine, RolUsuario.ADMIN)
    r = client.get(RUTA, params={"site_id": "shackleton", "paso_horas": "1"})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "PASO_HORAS_INVALIDO"
    assert client.get(RUTA, params={"site_id": "shackleton", "paso_horas": "24"}).status_code == 200

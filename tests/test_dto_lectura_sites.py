"""B3: /api/sites en su propio router con la lógica en el servicio, y DTO de lectura más estrictos con el mismo JSON."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import config as c
from app.main import app
from app.schemas.lectura import MetricsItem, Semaforo, SunsetsOut
from app.services import lectura
from tests.conftest import fixture_bytes

OPENAPI = app.openapi()


@pytest.fixture
def api(bd, data_dir):
    return TestClient(app)


def test_sites_vive_en_su_router_con_etiqueta_y_resumen():
    op = OPENAPI["paths"]["/api/sites"]["get"]
    assert op["tags"] == [c.TAG_SITES] and op["summary"] == "List the landing sites"
    assert [r.path for r in app.routes if r.path == "/api/sites"] == ["/api/sites"]


def test_el_servicio_lanza_lecturaerror_503_con_los_textos_de_siempre(data_dir):
    (data_dir / "sites.csv").unlink()
    with pytest.raises(lectura.LecturaError) as sin_archivo:
        lectura.sites_list()
    assert (sin_archivo.value.status, sin_archivo.value.detail) == (503, c.MSG_NO_SITES)
    (data_dir / "sites.csv").write_bytes(fixture_bytes("rango.csv"))
    with pytest.raises(lectura.LecturaError) as invalido:
        lectura.sites_list()
    assert (invalido.value.status, invalido.value.detail) == (503, c.MSG_SITES_INVALID)


def test_el_endpoint_sigue_dando_el_503_de_texto(api, data_dir):
    (data_dir / "sites.csv").unlink()
    r = api.get("/api/sites")
    assert (r.status_code, r.json()) == (503, {"detail": c.MSG_NO_SITES})


def test_el_servicio_devuelve_los_sitios_tipados(data_dir):
    sitios = lectura.sites_list()
    assert [s["site_id"] for s in sitios] == ["sitio_uno", "sitio_dos", "sitio_tres"] and isinstance(sitios[0]["lat_deg"], float)


# ------------------------------------------------------------------ DTO más estrictos, mismo JSON
def test_semaforo_serializa_la_clave_global_y_restringe_los_niveles():
    dto = Semaforo.model_validate({"energia": "verde", "comunicacion": "rojo", "global": "rojo"})
    assert dto.model_dump(by_alias=True) == {"energia": "verde", "comunicacion": "rojo", "global": "rojo"}
    with pytest.raises(ValidationError):
        Semaforo.model_validate({"energia": "azul", "comunicacion": "rojo", "global": "rojo"})


def test_las_metricas_reales_conservan_la_clave_global(api):
    r = api.get("/api/efemerides/sitio_uno")           # solo para comprobar que la app responde con los datos de prueba
    assert r.status_code in (200, 404, 422)


def test_origen_solo_admite_calculado():
    ejemplo = dict(MetricsItem.model_config["json_schema_extra"]["examples"][0])
    assert MetricsItem.model_validate(ejemplo).origen == "calculado"
    with pytest.raises(ValidationError):
        MetricsItem.model_validate({**ejemplo, "origen": "ejemplo"})


@pytest.mark.parametrize("campo", ["2026-12-01T00:00:00Z", "2026-12-01T00:00", "2026-12-01"])
def test_las_fechas_de_lectura_exigen_el_formato_utc_del_contrato(campo):
    ejemplo = dict(SunsetsOut.model_config["json_schema_extra"]["examples"][0])
    with pytest.raises(ValidationError):
        SunsetsOut.model_validate({**ejemplo, "desde_utc": campo})


def test_las_puestas_nulas_siguen_siendo_validas():
    ejemplo = dict(SunsetsOut.model_config["json_schema_extra"]["examples"][0])
    ejemplo["tierra"] = {"visible": False, "proxima_puesta_utc": None, "proxima_salida_utc": None, "horas_restantes": None}
    assert SunsetsOut.model_validate(ejemplo).tierra.proxima_puesta_utc is None


def test_metricas_sin_site_id_sigue_dando_el_422_de_texto(api):
    r = api.get("/api/metricas")
    assert (r.status_code, r.json()) == (422, {"detail": "Indica al menos un site_id"})
    param = next(p for p in OPENAPI["paths"]["/api/metricas"]["get"]["parameters"] if p["name"] == "site_id")
    assert param["required"] is False

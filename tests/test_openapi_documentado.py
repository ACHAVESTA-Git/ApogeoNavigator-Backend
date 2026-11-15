"""/docs para el jurado: descripción, etiquetas, resúmenes y ejemplos válidos en los DTO principales."""
import pytest

from app.core import config as c
from app.main import app
from app.schemas.auditoria import EventoOut
from app.schemas.lectura import EphemerisOut, MetricsItem, SunsetsOut
from app.schemas.report import Issue, Report
from app.schemas.sites import Site

ESQUEMA = app.openapi()
OPERACIONES = [(ruta, metodo, op) for ruta, ms in ESQUEMA["paths"].items() for metodo, op in ms.items()]
DTO_CON_EJEMPLO = [Site, EphemerisOut, MetricsItem, SunsetsOut, Report, Issue, EventoOut]


def test_la_api_tiene_descripcion_contacto_y_licencia():
    info = ESQUEMA["info"]
    assert "NASA/JPL Horizons" in info["description"] and "UTC" in info["description"] and len(info["description"]) > 400
    assert info["contact"]["name"] == "Apogeo Team" and info["license"]["name"] == "MIT"


@pytest.mark.parametrize("ruta,metodo,op", OPERACIONES, ids=[f"{m.upper()} {r}" for r, m, _ in OPERACIONES])
def test_cada_operacion_tiene_etiqueta_y_resumen(ruta, metodo, op):
    assert op.get("tags"), f"{metodo.upper()} {ruta} sin etiqueta"
    assert op.get("summary") and op["summary"] != op.get("operationId"), f"{metodo.upper()} {ruta} sin resumen propio"
    assert op["summary"][0].isupper() and not op["summary"].endswith(".")


def test_toda_etiqueta_usada_esta_declarada_con_descripcion():
    declaradas = {t["name"]: t for t in ESQUEMA["tags"]}
    usadas = {t for _, _, op in OPERACIONES for t in op["tags"]}
    assert usadas <= set(declaradas), usadas - set(declaradas)
    assert all(declaradas[t].get("description") for t in usadas)
    assert {c.TAG_SYSTEM, c.TAG_SITES} <= usadas            # health y sitios ya no quedan sin etiqueta


@pytest.mark.parametrize("modelo", DTO_CON_EJEMPLO, ids=lambda m: m.__name__)
def test_el_ejemplo_del_dto_aparece_en_openapi_y_es_valido(modelo):
    esquema = ESQUEMA["components"]["schemas"][modelo.__name__]
    assert esquema.get("examples"), f"{modelo.__name__} sin ejemplo"
    # Se valida el ejemplo de origen: pydantic omite las claves null al publicarlo en OpenAPI.
    for ejemplo in modelo.model_config["json_schema_extra"]["examples"]:
        assert modelo.model_validate(ejemplo)               # el ejemplo cumple el propio modelo


def test_los_ejemplos_son_datos_reales_de_shackleton_y_apolo_11():
    assert EphemerisOut.model_config["json_schema_extra"]["examples"][0]["site_id"] == "shackleton"
    metrica = MetricsItem.model_config["json_schema_extra"]["examples"][0]
    assert (metrica["pct_tierra"], metrica["sol_el_max_deg"]) == (39.11, 1.7385)
    assert Site.model_config["json_schema_extra"]["examples"][0]["site_id"] == "apollo11"


def test_los_campos_con_unidad_tienen_descripcion():
    for modelo in (EphemerisOut, MetricsItem, SunsetsOut, Site):
        props = ESQUEMA["components"]["schemas"][modelo.__name__]["properties"]
        for nombre, prop in props.items():
            if nombre.endswith(("_deg", "_utc", "_km")) or nombre.startswith("pct_") or nombre.startswith("horas_"):
                assert prop.get("description"), f"{modelo.__name__}.{nombre} sin descripción"


@pytest.mark.parametrize("ruta", ["/api/efemerides/{site_id}", "/api/metricas", "/api/puestas/{site_id}"])
def test_los_parametros_de_lectura_tienen_descripcion_y_ejemplo(ruta):
    for parametro in ESQUEMA["paths"][ruta]["get"]["parameters"]:
        assert parametro.get("description"), f"{ruta}: {parametro['name']} sin descripción"
        assert parametro["schema"].get("examples") or parametro.get("examples") or parametro["schema"].get("example"), (ruta, parametro["name"])


def test_los_parametros_de_lectura_no_ganan_restricciones_que_cambien_los_errores():
    """Con pattern, ge/le o datetime, el 422 pasaría de texto a lista y el contrato del frontend se rompería."""
    for ruta in ("/api/efemerides/{site_id}", "/api/metricas", "/api/puestas/{site_id}"):
        for parametro in ESQUEMA["paths"][ruta]["get"]["parameters"]:
            esquema = parametro["schema"]
            base = esquema.get("items", esquema) if esquema.get("type") == "array" else esquema
            otras = esquema.get("anyOf", [])
            assert not {"pattern", "minimum", "maximum", "format"} & set(base), (ruta, parametro["name"])
            assert all(not {"pattern", "format"} & set(o) for o in otras), (ruta, parametro["name"])

"""/docs para el jurado: descripción, etiquetas, resúmenes y ejemplos válidos en los DTO principales."""
import pytest

from app.core import config as c
from app.main import app
from app.schemas.auditoria import EventoOut
from app.schemas.lectura import EphemerisOut, MetricsItem, SunsetsOut
from app.schemas.report import Issue, Report
from app.schemas.simulacion import SimulacionOut
from app.schemas.sites import Site

ESQUEMA = app.openapi()
OPERACIONES = [(ruta, metodo, op) for ruta, ms in ESQUEMA["paths"].items() for metodo, op in ms.items()]
DTO_CON_EJEMPLO = [Site, EphemerisOut, MetricsItem, SunsetsOut, Report, Issue, EventoOut, SimulacionOut]


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


@pytest.mark.parametrize("ruta", ["/api/efemerides/{site_id}", "/api/metricas", "/api/puestas/{site_id}", "/api/simulacion/{site_id}"])
def test_los_parametros_de_lectura_tienen_descripcion_y_ejemplo(ruta):
    for parametro in ESQUEMA["paths"][ruta]["get"]["parameters"]:
        assert parametro.get("description"), f"{ruta}: {parametro['name']} sin descripción"
        assert parametro["schema"].get("examples") or parametro.get("examples") or parametro["schema"].get("example"), (ruta, parametro["name"])


def test_los_parametros_de_lectura_no_ganan_restricciones_que_cambien_los_errores():
    """Con pattern, ge/le o datetime, el 422 pasaría de texto a lista y el contrato del frontend se rompería."""
    for ruta in ("/api/efemerides/{site_id}", "/api/metricas", "/api/puestas/{site_id}", "/api/simulacion/{site_id}"):
        for parametro in ESQUEMA["paths"][ruta]["get"]["parameters"]:
            esquema = parametro["schema"]
            base = esquema.get("items", esquema) if esquema.get("type") == "array" else esquema
            otras = esquema.get("anyOf", [])
            assert not {"pattern", "minimum", "maximum", "format"} & set(base), (ruta, parametro["name"])
            assert all(not {"pattern", "format"} & set(o) for o in otras), (ruta, parametro["name"])


PALABRAS_EN_ESPANOL = (" el ", " la ", " los ", " las ", " del ", " con ", " sin ", " según ", " para ", " por ", " si ", " una ", " un ")


def _descripciones():
    """Todas las descripciones de campos de los DTO y de parámetros de lectura y simulación que salen en /docs."""
    for nombre, esquema in ESQUEMA["components"]["schemas"].items():
        for campo, prop in esquema.get("properties", {}).items():
            if prop.get("description"):
                yield f"{nombre}.{campo}", prop["description"]
    for ruta in ("/api/efemerides/{site_id}", "/api/metricas", "/api/puestas/{site_id}", "/api/simulacion/{site_id}"):
        for p in ESQUEMA["paths"][ruta]["get"]["parameters"]:
            yield f"{ruta}:{p['name']}", p.get("description", "")
        yield f"{ruta}:doc", ESQUEMA["paths"][ruta]["get"].get("description", "")


@pytest.mark.parametrize("donde,texto", [d for d in _descripciones() if not d[0].startswith(("Body_", "HTTPValidation", "ValidationError", "ClaveOut", "ClaveEntregada", "Entrega", "GenerarIn", "RevocarIn", "RotarIn", "PublicaOut", "HuellaOut", "VerificacionOut", "EventoOut"))],
                         ids=lambda x: x if len(x) < 60 else x[:57] + "...")
def test_la_documentacion_de_los_dto_y_parametros_publicos_esta_en_ingles(donde, texto):
    """El jurado lee /docs en inglés. (Los DTO de auditoría y claves aún no llevan descripciones.)"""
    assert texto and not any(palabra in f" {texto.lower()} " for palabra in PALABRAS_EN_ESPANOL), f"{donde}: {texto}"


def test_la_simulacion_documenta_desde_hasta_y_paso_horas():
    params = {p["name"]: p for p in ESQUEMA["paths"]["/api/simulacion/{site_id}"]["get"]["parameters"]}
    assert set(params) == {"site_id", "desde", "hasta", "paso_horas"}
    assert "0.25" in params["paso_horas"]["description"] and "366" in params["hasta"]["description"]
    assert all(p["schema"].get("examples") or p.get("examples") for p in params.values())
    assert SimulacionOut.model_config["json_schema_extra"]["examples"][0]["site_id"] == "shackleton"

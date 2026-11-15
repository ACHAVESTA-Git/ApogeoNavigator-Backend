"""GET /api/analisis/{site_id}: transparencia del análisis, con datos reales y con datos subidos por un usuario."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analysis import transparencia
from app.core import config as c
from app.main import app
from app.security import pqc
from app.services import lectura
from app.utils import auditoria_util as au
from tests.conftest import API_KEY, upload

REAL_DATA = Path(__file__).resolve().parents[1] / "data"
HEADERS = {"X-API-Key": API_KEY}
STEP_IDS = [step[0] for step in c.ANALISIS_STEPS]
pytestmark = pytest.mark.skipif(not (REAL_DATA / "sites.csv").is_file(), reason="no hay data/sites.csv")


@pytest.fixture
def stack(tmp_path, monkeypatch, bd, pqc_keys):
    """App completa con los datos reales copiados, una semilla idéntica, base de datos SQLite y claves de prueba."""
    data, seed = tmp_path / "data", tmp_path / "seed"
    data.mkdir(), seed.mkdir()
    for f in REAL_DATA.glob("*.csv"):
        shutil.copy(f, data / f.name)
        shutil.copy(f, seed / f.name)
    for var in ("K_SERVICE", "ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    for name, value in (("DATA_DIR", str(data)), ("DATA_SEED_DIR", str(seed)), ("UPLOAD_API_KEY", API_KEY), ("IP_HASH_SALT", "sal"),
                        ("PQC_PRIVATE_KEY", pqc_keys[0]), ("PQC_PUBLIC_KEY", pqc_keys[1])):
        monkeypatch.setenv(name, value)
    pqc.reiniciar()
    au.set_firmante()
    lectura.clear_cache()
    return type("Stack", (), {"client": TestClient(app, raise_server_exceptions=False), "data": data, "seed": seed})


def get(stack, site="shackleton", **params):
    return stack.client.get(f"/api/analisis/{site}", params=params)


# ------------------------------------------------------------------ contenido
def test_el_pipeline_tiene_los_siete_pasos_en_orden_con_sus_numeros(stack):
    body = get(stack).json()
    assert [p["id"] for p in body["pasos"]] == STEP_IDS == ["carga", "validacion_csv", "features", "fft", "ajuste",
                                                             "validacion_horizons", "proyeccion"]
    assert all(p["nombre"] and p["descripcion"] and p["resultado"] for p in body["pasos"])
    assert {p["estado"] for p in body["pasos"]} == {c.STEP_OK}
    carga = {m["nombre"]: m for m in body["pasos"][0]["resultado"]}
    assert carga[c.LBL_ROWS]["valor"] == 4369 and carga[c.LBL_ROWS]["unidad"] == c.UNIT_ROWS


def test_entrada_con_rango_paso_calidad_y_huella(stack):
    entrada = get(stack, "apollo15").json()["entrada"]
    assert (entrada["archivo"], entrada["filas"], entrada["paso_horas"]) == ("efemerides_apollo15.csv", 4369, 1.0)
    assert (entrada["inicio_utc"], entrada["fin_utc"]) == ("2026-12-01T00:00Z", "2027-06-01T00:00Z")
    assert entrada["calidad"] == {"filas_validas": 4369, "filas_con_error": 0, "huecos": 0, "paso_constante": True}
    assert len(entrada["origen"]["sha3_256"]) == 64 and entrada["bytes"] == (stack.data / "efemerides_apollo15.csv").stat().st_size


def test_el_espectro_senala_el_mes_sinodico(stack):
    espectro = get(stack, "apollo15").json()["espectro"]
    sun = next(s for s in espectro["series"] if s["variable"] == "sol_el_deg")
    assert len(sun["picos"]) <= c.ANALISIS_SPECTRUM_PEAKS
    top = sun["picos"][0]
    assert top["es_sinodico"] is True and top["periodo_dias"] == pytest.approx(29.53, abs=0.3) and top["amplitud_rel"] == 1.0
    assert espectro["sinodico_teorico_dias"] == c.SYNODIC_MONTH_DAYS
    assert espectro["sinodico_afinado_dias"] == pytest.approx(29.53, abs=0.3)
    assert espectro["resolucion_dias"] == pytest.approx(c.SYNODIC_MONTH_DAYS ** 2 / espectro["ventana_dias"], abs=0.01)
    assert "no separa" in espectro["nota"]


def test_modelo_con_periodos_terminos_r2_y_residuos(stack):
    model = get(stack, "apollo15").json()["modelo"]
    assert model["terminos_por_cuerpo"] == {"Sol": 28, "Tierra": 12}          # 14 y 6 frecuencias, con coseno y seno
    assert model["ajuste_directo_elevacion_sol"] is False
    polar = get(stack, "shackleton").json()["modelo"]                      # el ajuste directo de la elevación añade otros 28
    assert polar["ajuste_directo_elevacion_sol"] is True and polar["terminos_por_cuerpo"]["Sol"] == 56
    periods = [t["periodo_dias"] for t in model["periodos"]]
    assert periods == sorted(periods, reverse=True) and all(t["rol"] for t in model["periodos"])
    assert {t["cuerpo"] for t in model["periodos"]} == {"Sol", "Tierra"}
    assert any("sinódico" in t["rol"] for t in model["periodos"]) and any("anomalístico" in t["rol"] for t in model["periodos"])
    assert set(model["r2"]) == set(model["residuos"]) == {"sol_az_deg", "sol_el_deg", "tierra_az_deg", "tierra_el_deg"}
    assert all(0.9 < v <= 1.0 for v in model["r2"].values())
    res = model["residuos"]["sol_el_deg"]
    assert set(res) == {"rmse_deg", "mae_deg", "sesgo_deg", "max_abs_deg", "p95_abs_deg"} and res["rmse_deg"] < 1.0


def test_validacion_vigente_con_los_datos_de_la_semilla(stack):
    v = get(stack, "shackleton").json()["validacion"]
    table = json.loads(c.ERRORS_PATH.read_text(encoding="utf-8"))["sitios"]["shackleton"]
    assert v["disponible"] is True and v["vigente"] is True and v["motivo"] is None
    assert v["rmse_deg"]["sol_el_deg"] == table["sol_el_deg"]["rmse_deg"]
    assert v["mae_deg"]["tierra_az_deg"] == table["tierra_az_deg"]["mae_deg"]
    assert v["mejora_frente_a_ingenua"]["sol_el_deg"] > 1 and len(v["rmse_por_mes_deg"]["sol_el_deg"]) == 12
    assert v["semaforo_aciertos"]["meses"] == 12 and v["semaforo_aciertos"]["aciertos_global"] >= 10
    assert (v["validacion_desde_utc"], v["validacion_hasta_utc"]) == ("2027-06-01T00:00Z", "2028-06-01T00:00Z")


@pytest.mark.parametrize("site,applies", [("shackleton", {"sol_rasante_polo"}), ("apollo11", {"cenit_ecuador"}),
                                          ("apollo15", set())])
def test_las_limitaciones_aplican_segun_los_datos_del_sitio(stack, site, applies):
    limits = get(stack, site).json()["limitaciones"]
    always = {"horizonte_ideal", "no_sustituye_efemerides", "ventana_corta", "validacion_no_ciega"}
    assert {x["codigo"] for x in limits if x["aplica"]} == always | applies
    assert all(x["mensaje"] for x in limits)


def test_la_serie_es_pequena_submuestreada_y_separa_observado_ajustado_proyectado(stack):
    serie = get(stack).json()["serie"]
    points = serie["puntos"]
    assert serie["variable"] == "sol_el_deg" and serie["fin_datos_utc"] == "2027-06-01T00:00Z"
    assert len(points) <= c.ANALISIS_SERIES_POINTS + 1 and serie["paso_horas"] >= 1
    inside = [p for p in points if p["utc"] <= serie["fin_datos_utc"]]
    outside = [p for p in points if p["utc"] > serie["fin_datos_utc"]]
    assert inside and outside
    assert all(p["observado"] is not None and p["ajustado"] is not None and p["proyectado"] is None for p in inside)
    assert all(p["observado"] is None and p["ajustado"] is None and p["proyectado"] is not None for p in outside)
    assert max(abs(p["observado"] - p["ajustado"]) for p in inside) < 1.0


def test_serie_de_otra_variable_y_rango_propio(stack):
    serie = get(stack, "apollo15", variable="tierra_az_deg", desde="2027-01-01T00:00Z", hasta="2027-07-01T00:00Z").json()["serie"]
    assert serie["variable"] == "tierra_az_deg" and serie["nombre"] == "Azimut de la Tierra"
    assert serie["puntos"][0]["utc"] == "2027-01-01T00:00Z" and serie["puntos"][-1]["utc"] <= "2027-07-01T00:00Z"


# ------------------------------------------------------------------ origen y datos subidos por el usuario
def test_datos_identicos_a_la_semilla_son_de_nasa(stack):
    origin = get(stack).json()["entrada"]["origen"]
    assert origin["tipo"] == c.ORIGIN_NASA_SEED and origin["coincide_con_semilla"] is True and origin["ultima_carga"] is None


def test_sin_semilla_ni_registro_no_se_afirma_nada(stack, monkeypatch):
    monkeypatch.delenv("DATA_SEED_DIR")
    lectura.clear_cache()
    origin = get(stack).json()["entrada"]["origen"]
    assert origin["tipo"] == c.ORIGIN_UNRECORDED and origin["coincide_con_semilla"] is None and len(origin["sha3_256"]) == 64


def _shorter(site: str, days: int) -> bytes:
    lines = (REAL_DATA / f"efemerides_{site}.csv").read_text(encoding="utf-8").splitlines()
    return ("\n".join(lines[: 1 + 24 * days]) + "\n").encode()


def test_unas_efemerides_subidas_por_un_usuario_cambian_el_analisis(stack):
    before = get(stack, "slim").json()
    assert before["entrada"]["filas"] == 4369 and before["entrada"]["origen"]["tipo"] == c.ORIGIN_NASA_SEED
    new = _shorter("slim", 120)
    assert upload(stack.client, "efemerides", new, "efemerides_slim.csv", dry_run=False).status_code == 200
    after = get(stack, "slim").json()
    assert after["entrada"]["filas"] == 24 * 120 and after["entrada"]["fin_utc"] != before["entrada"]["fin_utc"]
    assert after["serie"]["fin_datos_utc"] == after["entrada"]["fin_utc"]
    assert after["entrada"]["origen"]["sha3_256"] != before["entrada"]["origen"]["sha3_256"]
    # el origen se demuestra con el registro de la carga, sin identidades
    origin = after["entrada"]["origen"]
    assert origin["tipo"] == c.ORIGIN_UPLOADED and origin["coincide_con_semilla"] is False
    assert origin["ultima_carga"]["coincide"] is True and origin["ultima_carga"]["cargado_por"] == c.UPLOADER_API_KEY
    assert origin["ultima_carga"]["sha3_256"] == origin["sha3_256"] and origin["ultima_carga"]["archivo"] == "efemerides_slim.csv"
    assert origin["manifiesto"]["coincide"] is True and origin["manifiesto"]["sha3_256"] == origin["sha3_256"]
    assert origin["manifiesto"]["firmado"] is True and origin["manifiesto"]["firma_nota"] is None      # firma VÁLIDA, no solo presente
    # la validación contra Horizons era de otros datos: se dice, no se disimula
    assert after["validacion"]["disponible"] is True and after["validacion"]["vigente"] is False
    assert next(x for x in after["limitaciones"] if x["codigo"] == "validacion_de_otros_datos")["aplica"] is True
    states = {p["id"]: p["estado"] for p in after["pasos"]}
    assert states["validacion_horizons"] == states["proyeccion"] == c.STEP_WARNING


def test_un_archivo_editado_a_mano_se_marca_como_modificado(stack):
    (stack.data / "efemerides_slim.csv").write_bytes(_shorter("slim", 100))
    origin = get(stack, "slim").json()["entrada"]["origen"]
    assert origin["tipo"] == c.ORIGIN_MODIFIED and origin["coincide_con_semilla"] is False and origin["ultima_carga"] is None


def test_la_carga_rechazada_no_cambia_el_analisis(stack):
    before = get(stack, "slim").json()["entrada"]
    bad = b"utc,sol_az_deg\n2026-12-01T00:00Z,1\n"
    assert upload(stack.client, "efemerides", bad, "efemerides_slim.csv", dry_run=False).status_code == 422
    assert get(stack, "slim").json()["entrada"] == before


def test_el_analisis_se_cachea_y_se_recalcula_al_cambiar_el_archivo(stack, monkeypatch):
    calls = []
    real = transparencia._analyse
    monkeypatch.setattr(transparencia, "_analyse", lambda *a: (calls.append(1), real(*a))[1])
    get(stack, "slim"), get(stack, "slim")
    assert len(calls) == 1
    assert upload(stack.client, "efemerides", _shorter("slim", 90), "efemerides_slim.csv", dry_run=False).status_code == 200
    get(stack, "slim")
    assert len(calls) == 2


def test_sin_base_de_datos_el_analisis_sigue_funcionando(stack, monkeypatch):
    from app.db import session as db_session
    db_session.configure(None)
    body = get(stack).json()
    assert body["entrada"]["origen"]["ultima_carga"] is None and body["entrada"]["origen"]["tipo"] == c.ORIGIN_NASA_SEED


# ------------------------------------------------------------------ validación no disponible
def _with_table(monkeypatch, tmp_path, mutate=None, missing=False):
    path = tmp_path / "errores.json"
    if not missing:
        table = json.loads(c.ERRORS_PATH.read_text(encoding="utf-8"))
        if mutate:
            mutate(table)
        path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(c, "ERRORS_PATH", path)
    lectura.clear_cache()


def test_un_sitio_sin_validacion_lo_dice_y_el_analisis_sigue(stack, monkeypatch, tmp_path):
    _with_table(monkeypatch, tmp_path, lambda t: t["sitios"].pop("slim"))
    body = get(stack, "slim")
    assert body.status_code == 200
    v = body.json()["validacion"]
    assert v["disponible"] is False and v["motivo"] == c.MSG_ANALYSIS_NO_VALIDATION and v["rmse_deg"] == {}
    assert [p["estado"] for p in body.json()["pasos"][-2:]] == [c.STEP_UNAVAILABLE] * 2


def test_tabla_de_errores_ausente_o_corrupta_no_tumba_el_analisis(stack, monkeypatch, tmp_path):
    _with_table(monkeypatch, tmp_path, missing=True)
    assert get(stack).json()["validacion"]["motivo"] == c.MSG_SIM_ERRORS_MISSING
    path = tmp_path / "corrupta.json"
    path.write_text("{no es json", encoding="utf-8")
    monkeypatch.setattr(c, "ERRORS_PATH", path)
    lectura.clear_cache()
    body = get(stack)
    assert body.status_code == 200 and body.json()["validacion"] == {**body.json()["validacion"], "disponible": False}
    assert body.json()["validacion"]["motivo"] == c.MSG_SIM_ERRORS_INVALID


# ------------------------------------------------------------------ errores {"detail"}
@pytest.mark.parametrize("site,params,status", [
    ("no_existe", {}, 404),
    ("shackleton", {"variable": "temperatura"}, 422),
    ("shackleton", {"desde": "2026-11-01T00:00Z"}, 422),
    ("shackleton", {"desde": "2027-01-01T00:00Z", "hasta": "2027-01-01T00:00Z"}, 422),
    ("shackleton", {"desde": "2027-01-01T00:00Z", "hasta": "2026-12-01T00:00Z"}, 422),
    ("shackleton", {"hasta": "2028-07-01T00:00Z"}, 422),
    ("shackleton", {"desde": "ayer"}, 422),
    ("shackleton", {"hasta": "2027-01-01 00:00"}, 422),
])
def test_errores_con_detail_de_texto(stack, site, params, status):
    response = get(stack, site, **params)
    assert response.status_code == status and isinstance(response.json()["detail"], str)


def test_sitio_sin_archivo_de_efemerides_es_404_y_archivo_invalido_503(stack):
    (stack.data / "efemerides_slim.csv").unlink()
    assert get(stack, "slim").status_code == 404
    (stack.data / "efemerides_slim.csv").write_text("utc,sol_az_deg\n2026-12-01T00:00Z,1\n", encoding="utf-8")
    assert get(stack, "slim").status_code == 503


# ------------------------------------------------------------------ «firmado» significa firma válida
def _after_upload(stack):
    assert upload(stack.client, "efemerides", _shorter("slim", 90), "efemerides_slim.csv", dry_run=False).status_code == 200
    return json.loads((stack.data / c.MANIFEST_NAME).read_text(encoding="utf-8"))


def _manifest(stack) -> dict:
    return get(stack, "slim").json()["entrada"]["origen"]["manifiesto"]


def test_un_manifiesto_con_la_firma_valida_se_marca_firmado(stack):
    _after_upload(stack)
    assert (_manifest(stack)["firmado"], _manifest(stack)["firma_nota"]) == (True, None)


def test_un_manifiesto_manipulado_no_se_marca_firmado_aunque_tenga_el_campo_de_firma(stack):
    manifest = _after_upload(stack)
    assert manifest[pqc.MANIFEST_SIGNATURE_FIELD]                              # el campo existe...
    manifest["generado_utc"] = "2030-01-01T00:00:00Z"                           # ...pero el contenido ya no es el firmado
    (stack.data / c.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    entry = _manifest(stack)
    assert entry["firmado"] is False and "no es válida" in entry["firma_nota"]


def test_un_manifiesto_con_la_firma_alterada_no_es_firmado(stack):
    manifest = _after_upload(stack)
    signature = manifest[pqc.MANIFEST_SIGNATURE_FIELD]
    manifest[pqc.MANIFEST_SIGNATURE_FIELD] = ("A" if signature[0] != "A" else "B") + signature[1:]
    (stack.data / c.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    assert _manifest(stack)["firmado"] is False


def test_un_manifiesto_sin_firma_no_es_firmado(stack):
    manifest = _after_upload(stack)
    del manifest[pqc.MANIFEST_SIGNATURE_FIELD]
    (stack.data / c.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    entry = _manifest(stack)
    assert entry["firmado"] is False and pqc.MSG_MANIFEST_NO_SIGNATURE in entry["firma_nota"]


def test_si_no_se_puede_verificar_la_firma_es_null_con_explicacion_nunca_true(stack, monkeypatch):
    _after_upload(stack)

    def no_key():
        raise pqc.ErrorClavesPQC("sin clave")
    monkeypatch.setattr(pqc, "clave_publica", no_key)
    entry = _manifest(stack)
    assert entry["firmado"] is None and entry["firma_nota"] == c.MSG_MANIFEST_UNVERIFIABLE


def test_con_otra_clave_de_firma_el_manifiesto_ya_no_se_da_por_firmado(stack, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    _after_upload(stack)
    other = MLDSA65PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setattr(pqc, "clave_publica", lambda: other)
    entry = _manifest(stack)
    assert entry["firmado"] is False and entry["firma_nota"]


# ------------------------------------------------------------------ textos que no pueden mentir
def test_la_descripcion_de_la_validacion_depende_de_si_es_vigente(stack):
    fresh = {p["id"]: p["descripcion"] for p in get(stack, "slim").json()["pasos"]}
    assert fresh["validacion_horizons"] == c.DESC_VALIDATION_CURRENT and fresh["proyeccion"] == c.DESC_PROJECTION_CURRENT
    assert "entrenado solo con estos datos" in fresh["validacion_horizons"]
    _after_upload(stack)
    stale = {p["id"]: p["descripcion"] for p in get(stack, "slim").json()["pasos"]}
    assert stale["validacion_horizons"] == c.DESC_VALIDATION_STALE and stale["proyeccion"] == c.DESC_PROJECTION_STALE
    assert "entrenado solo con estos datos" not in stale["validacion_horizons"] and "semilla" in stale["validacion_horizons"]


def test_sin_validacion_las_descripciones_lo_dicen(stack, monkeypatch, tmp_path):
    _with_table(monkeypatch, tmp_path, lambda t: t["sitios"].pop("slim"))
    texts = {p["id"]: p["descripcion"] for p in get(stack, "slim").json()["pasos"]}
    assert texts["validacion_horizons"] == c.DESC_VALIDATION_MISSING and texts["proyeccion"] == c.DESC_PROJECTION_MISSING


def _long_series(site: str, days: int) -> bytes:
    from tests.test_analisis_ajuste import synthetic_frame
    rows = synthetic_frame(days).collect()
    header = "utc,sol_az_deg,sol_el_deg,tierra_az_deg,tierra_el_deg"
    lines = [f"{r['utc']:%Y-%m-%dT%H:%MZ},{r['sol_az_deg']:.6f},{r['sol_el_deg']:.6f},{r['tierra_az_deg']:.6f},{r['tierra_el_deg']:.6f}"
             for r in rows.iter_rows(named=True)]
    return ("\n".join([header, *lines]) + "\n").encode()


def test_la_limitacion_de_ventana_corta_usa_la_ventana_real(stack):
    short = next(x for x in get(stack, "slim").json()["limitaciones"] if x["codigo"] == "ventana_corta")
    assert short["aplica"] is True and "182 días" in short["mensaje"] and "346.6" in short["mensaje"]
    assert upload(stack.client, "efemerides", _long_series("slim", 360), "efemerides_slim.csv", dry_run=False).status_code == 200
    body = get(stack, "slim").json()
    long_ = next(x for x in body["limitaciones"] if x["codigo"] == "ventana_corta")
    assert long_["aplica"] is False and "360 días" in long_["mensaje"]
    assert body["entrada"]["filas"] == 360 * 24


def test_r2_y_residuos_llevan_la_nota_de_que_son_del_ajuste_dentro_de_los_datos(stack):
    model = get(stack).json()["modelo"]
    assert model["nota_ajuste"] == c.NOTE_FIT_IN_SAMPLE and "no miden" in model["nota_ajuste"]
    schema = app.openapi()["components"]["schemas"]["Modelo"]["properties"]
    assert schema["r2"]["description"] == schema["residuos"]["description"] == c.NOTE_FIT_IN_SAMPLE_DOC
    assert model["r2"]["tierra_el_deg"] > 0.99          # el R² da ~1: por eso no debe leerse como acierto fuera de los datos


@pytest.mark.parametrize("site,params,status,code", [
    ("no_existe", {}, 404, "SITIO_NO_ENCONTRADO"),
    ("shackleton", {"variable": "temperatura"}, 422, "VARIABLE_INVALIDA"),
    ("shackleton", {"desde": "2026-11-01T00:00Z"}, 422, "FECHA_ANTES_DE_LOS_DATOS"),
    ("shackleton", {"hasta": "2028-07-01T00:00Z"}, 422, "FECHA_FUERA_DE_PROYECCION"),
    ("shackleton", {"desde": "2027-01-01T00:00Z", "hasta": "2026-12-01T00:00Z"}, 422, "RANGO_FECHAS_INVALIDO"),
    ("shackleton", {"desde": "ayer"}, 422, "RANGO_FECHAS_INVALIDO"),
])
def test_los_errores_del_analisis_llevan_su_objeto_error_con_codigo_de_dominio(stack, site, params, status, code):
    body = get(stack, site, **params)
    error = body.json()["error"]
    assert body.status_code == status and error["codigo"] == code and error["estado"] == status
    assert error["titulo"] and error["mensaje"] and error["accion"] and isinstance(body.json()["detail"], str)


def test_sitio_sin_efemerides_y_datos_invalidos_tienen_su_codigo(stack):
    (stack.data / "efemerides_slim.csv").unlink()
    assert get(stack, "slim").json()["error"]["codigo"] == "EFEMERIDES_NO_ENCONTRADAS"
    (stack.data / "efemerides_slim.csv").write_text("utc,sol_az_deg\n2026-12-01T00:00Z,1\n", encoding="utf-8")
    body = get(stack, "slim")
    assert body.status_code == 503 and body.json()["error"]["codigo"] == "EFEMERIDES_INVALIDAS"

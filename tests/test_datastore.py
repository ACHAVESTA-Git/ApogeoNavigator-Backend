"""Datos en Cloud Storage: arranque, semilla, réplica local, guardado con precondiciones y errores."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.core import config as c
from app.main import app
from app.security import pqc
from app.services import datastore
from app.services.blobstore import StoreConflict, StoreError
from tests.conftest import FIXTURES, MemoryStore, fixture_bytes, report_of, upload

SEED_FILES = {"sites.csv": fixture_bytes("sites_valido.csv"), "efemerides_sitio_uno.csv": fixture_bytes("efemerides_valido.csv")}


@pytest.fixture
def seed(tmp_path, monkeypatch):
    folder = tmp_path / "semilla"
    folder.mkdir()
    for name, data in SEED_FILES.items():
        (folder / name).write_bytes(data)
    monkeypatch.setenv("DATA_SEED_DIR", str(folder))
    return folder


@pytest.fixture
def local(tmp_path, monkeypatch, pqc_keys):
    """DATA_DIR vacío y claves PQC de prueba (sin sites.csv de la fixture data_dir)."""
    folder = tmp_path / "local"
    monkeypatch.setenv("DATA_DIR", str(folder))
    monkeypatch.setenv("UPLOAD_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("PQC_PRIVATE_KEY", pqc_keys[0])
    monkeypatch.setenv("PQC_PUBLIC_KEY", pqc_keys[1])
    pqc.reiniciar()
    yield folder
    pqc.reiniciar()


def with_store(monkeypatch, store):
    monkeypatch.setenv("DATA_BUCKET", "bucket-de-prueba")
    datastore.set_store(store)
    return store


# ------------------------------------------------------------------ arranque
def test_produccion_sin_bucket_falla_al_arrancar_con_mensaje_claro(local, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "apogeo-backend")
    with pytest.raises(RuntimeError, match="DATA_BUCKET"):
        datastore.startup()


def test_produccion_por_ENVIRONMENT_tambien_exige_bucket(local, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="efímero"):
        datastore.startup()


def test_desarrollo_sin_bucket_usa_el_disco_local_sin_error(local):
    datastore.startup()
    assert not local.exists() or not list(local.glob("*.csv"))


def test_sin_bucket_la_semilla_llena_un_data_dir_vacio(local, seed):
    datastore.startup()
    assert sorted(p.name for p in local.glob("*.csv")) == sorted(SEED_FILES)


def test_sin_bucket_la_semilla_no_pisa_datos_existentes(local, seed):
    local.mkdir()
    (local / "sites.csv").write_bytes(b"datos propios")
    datastore.startup()
    assert (local / "sites.csv").read_bytes() == b"datos propios"


def test_bucket_vacio_se_siembra_con_la_semilla_y_su_manifiesto(local, seed, monkeypatch):
    store = with_store(monkeypatch, MemoryStore())
    datastore.startup()
    assert store.names() == sorted([*SEED_FILES, "manifest.json"])
    assert sorted(p.name for p in local.glob("*.csv")) == sorted(SEED_FILES)
    manifest = json.loads(store.objects["manifest.json"][0])
    assert pqc.verificar_manifiesto(manifest, local) == []


def test_bucket_con_datos_manda_y_la_semilla_se_ignora(local, seed, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": b"del bucket"}))
    datastore.startup()
    assert store.names() == ["sites.csv"] and (local / "sites.csv").read_bytes() == b"del bucket"
    assert not (local / "efemerides_sitio_uno.csv").exists()


def test_el_arranque_borra_de_local_lo_que_no_esta_en_el_bucket(local, monkeypatch):
    local.mkdir()
    (local / "viejo.csv").write_bytes(b"x")
    with_store(monkeypatch, MemoryStore({"sites.csv": b"a"}))
    datastore.startup()
    assert sorted(p.name for p in local.glob("*.csv")) == ["sites.csv"]


def test_produccion_con_bucket_vacio_y_sin_claves_no_publica_datos_sin_firma(local, seed, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "x")
    monkeypatch.delenv("PQC_PRIVATE_KEY")
    monkeypatch.delenv("PQC_PUBLIC_KEY")
    pqc.reiniciar()
    with_store(monkeypatch, MemoryStore())
    with pytest.raises(pqc.ErrorClavesPQC):
        datastore.startup()


def test_el_bucket_inaccesible_en_el_arranque_hace_fallar(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore())
    store.fail = StoreError("sin red")
    with pytest.raises(StoreError):
        datastore.startup()


# ------------------------------------------------------------------ refresco de lecturas
def test_refresh_consulta_el_bucket_como_mucho_una_vez_por_intervalo(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": b"a"}))
    monkeypatch.setenv("DATA_REFRESH_SECONDS", "60")
    datastore.startup()
    llamadas = store.list_calls
    for _ in range(5):
        datastore.refresh_if_stale()
    assert store.list_calls == llamadas


def test_refresh_descarga_solo_lo_que_cambio(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": b"a", "otro.csv": b"b"}))
    monkeypatch.setenv("DATA_REFRESH_SECONDS", "0")
    datastore.startup()
    store._put("sites.csv", b"nuevo")
    descargas = []
    original = store.download
    store.download = lambda n: (descargas.append(n), original(n))[1]
    datastore.refresh_if_stale()
    assert descargas == ["sites.csv"] and (local / "sites.csv").read_bytes() == b"nuevo"


def test_refresh_si_el_bucket_cae_sigue_sirviendo_lo_local(local, monkeypatch, caplog):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": b"a"}))
    monkeypatch.setenv("DATA_REFRESH_SECONDS", "0")
    datastore.startup()
    store.fail = StoreError("caído")
    datastore.refresh_if_stale()                              # no lanza
    assert (local / "sites.csv").read_bytes() == b"a"


def test_otra_instancia_ve_lo_publicado_tras_el_intervalo(local, tmp_path, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    monkeypatch.setenv("DATA_REFRESH_SECONDS", "0")
    datastore.startup()
    datastore.guardar("sites.csv", fixture_bytes("sites_limites.csv"))
    otra = tmp_path / "instancia-b"                            # otra instancia: otro disco, mismo bucket
    monkeypatch.setenv("DATA_DIR", str(otra))
    datastore.set_store(store)
    datastore.startup()
    assert (otra / "sites.csv").read_bytes() == fixture_bytes("sites_limites.csv")
    assert pqc.verificar_manifiesto(json.loads((otra / "manifest.json").read_text()), otra) == []


# ------------------------------------------------------------------ guardado
def test_guardar_publica_csv_manifiesto_e_historial(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    datastore.startup()
    backup = datastore.guardar("sites.csv", fixture_bytes("sites_limites.csv"))
    assert store.objects["sites.csv"][0] == fixture_bytes("sites_limites.csv")
    assert f"_historial/{backup.name}" in store.objects and "manifest.json" in store.objects
    assert store.objects[f"_historial/{backup.name}"][0] == fixture_bytes("sites_valido.csv")
    assert pqc.verificar_manifiesto(json.loads(store.objects["manifest.json"][0]), local) == []


def test_guardar_un_archivo_nuevo_no_crea_historial(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    datastore.startup()
    assert datastore.guardar("efemerides_sitio_uno.csv", fixture_bytes("efemerides_valido.csv")) is None
    assert not [n for n in store.names() if n.startswith("_historial/")]


def test_conflicto_no_pisa_lo_del_otro_y_recarga_el_bucket(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    datastore.startup()
    original_upload = store.upload

    def otro_escribe_primero(name, data, **kw):               # otro proceso cambia sites.csv justo antes de la subida
        store.upload = original_upload
        store._put("sites.csv", b"cambio ajeno")
        return original_upload(name, data, **kw)
    store.upload = otro_escribe_primero
    with pytest.raises(datastore.DataConflict):
        datastore.guardar("sites.csv", fixture_bytes("sites_limites.csv"))
    assert store.objects["sites.csv"][0] == b"cambio ajeno"                   # no se pisó
    assert (local / "sites.csv").read_bytes() == b"cambio ajeno"             # local volvió a la verdad del bucket


def test_guardar_con_bucket_caido_lanza_StoreError(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    datastore.startup()
    store.fail = StoreError("caído")
    with pytest.raises(StoreError):
        datastore.guardar("sites.csv", fixture_bytes("sites_limites.csv"))


def test_sin_bucket_guardar_usa_el_disco(local):
    local.mkdir()
    datastore.guardar("sites.csv", fixture_bytes("sites_valido.csv"))
    assert (local / "sites.csv").read_bytes() == fixture_bytes("sites_valido.csv")


# ------------------------------------------------------------------ API con bucket
@pytest.fixture
def api(bd, data_dir, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    monkeypatch.setenv("DATA_REFRESH_SECONDS", "0")
    datastore.startup()
    return TestClient(app), store


def test_la_carga_llega_al_bucket_y_las_lecturas_la_ven(api):
    client, store = api
    assert upload(client, "sites", "sites_limites.csv", "sites.csv", dry_run=False).status_code == 200
    assert store.objects["sites.csv"][0] == fixture_bytes("sites_limites.csv")
    assert {s["site_id"] for s in client.get("/api/sites").json()} == {"lim_norte", "lim_sur", "lim_cero", "lim_min"}
    assert client.get("/api/seguridad/manifiesto").status_code == 200


def test_las_lecturas_ven_un_cambio_hecho_por_otra_instancia(api):
    client, store = api
    store._put("sites.csv", fixture_bytes("sites_limites.csv"))
    assert {s["site_id"] for s in client.get("/api/sites").json()} == {"lim_norte", "lim_sur", "lim_cero", "lim_min"}


def test_conflicto_responde_409(api, monkeypatch):
    client, store = api
    monkeypatch.setattr(datastore, "guardar", lambda *a, **k: (_ for _ in ()).throw(datastore.DataConflict(c.MSG_DATA_CONFLICT)))
    r = upload(client, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    assert r.status_code == 409 and r.json()["detail"] == c.MSG_DATA_CONFLICT


def test_bucket_caido_responde_503_y_no_deja_auditoria(api, monkeypatch):
    client, store = api
    monkeypatch.setattr(datastore, "guardar", lambda *a, **k: (_ for _ in ()).throw(StoreError("caído")))
    r = upload(client, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    assert r.status_code == 503 and r.json()["detail"] == c.MSG_BUCKET_UNAVAILABLE
    from app.db import session as db_session
    from app.db.models import EventoAuditoria
    with db_session.session_scope() as s:
        assert s.query(EventoAuditoria).count() == 0                       # la auditoría se deshizo


def test_arranque_de_la_aplicacion_en_produccion_con_bucket_y_sin_el_falla(bd, data_dir, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "x")
    monkeypatch.setenv("IP_HASH_SALT", "sal")
    with pytest.raises(RuntimeError, match="DATA_BUCKET"):
        with TestClient(app):
            pass


# ------------------------------------------------------------------ fallos SOLO en la subida (revisión del PR)
def _local_igual_al_bucket(store, folder):
    locales = {p.name: p.read_bytes() for p in folder.glob("*") if p.is_file() and not p.name.startswith(".")}
    remotos = {n: d for n, (d, _) in store.objects.items() if "/" not in n}
    return locales == remotos


@pytest.mark.parametrize("n_subida", [1, 2, 3], ids=["historial", "csv", "manifiesto"])
def test_si_falla_una_subida_la_replica_local_vuelve_a_ser_la_del_bucket(local, monkeypatch, n_subida):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    datastore.startup()
    store.fail_uploads = {n_subida: StoreError("caído a mitad")}
    with pytest.raises(StoreError):
        datastore.guardar("sites.csv", fixture_bytes("sites_limites.csv"))
    assert _local_igual_al_bucket(store, local)
    datastore.pull()                                       # un pull posterior tampoco deja datos que el bucket no tiene
    assert _local_igual_al_bucket(store, local)


def test_fallo_en_la_subida_del_csv_deja_el_bucket_intacto(local, monkeypatch):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    datastore.startup()
    store.fail_uploads = {2: StoreError("caído")}           # 1.ª = historial, 2.ª = CSV
    with pytest.raises(StoreError):
        datastore.guardar("sites.csv", fixture_bytes("sites_limites.csv"))
    assert store.objects["sites.csv"][0] == fixture_bytes("sites_valido.csv")
    assert (local / "sites.csv").read_bytes() == fixture_bytes("sites_valido.csv")


def test_si_tambien_falla_el_restablecimiento_se_conserva_el_error_original(local, monkeypatch, caplog):
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    datastore.startup()
    store.fail_uploads = {1: StoreError("subida")}
    original_list = store.list
    llamadas = []

    def lista_caida():
        llamadas.append(1)
        if len(llamadas) >= 2:                              # la 1.ª es el pull previo al guardado; la 2.ª, el restablecimiento
            raise StoreError("sigue caído")
        return original_list()
    store.list = lista_caida
    with pytest.raises(StoreError, match="subida"):
        datastore.guardar("sites.csv", fixture_bytes("sites_limites.csv"))
    assert "No se pudo restablecer" in caplog.text


def test_siembra_concurrente_no_tumba_el_arranque(local, seed, monkeypatch):
    store = with_store(monkeypatch, MemoryStore())
    store.on_upload = lambda name: store._put(name, b"sembrado por otra instancia") if name == "sites.csv" and "sites.csv" not in store.objects else None
    datastore.startup()                                    # la subida choca con la precondición (objeto ya creado): no lanza
    assert store.objects["sites.csv"][0] == b"sembrado por otra instancia"
    assert (local / "sites.csv").read_bytes() == b"sembrado por otra instancia"


def test_siembra_con_conflicto_en_el_manifiesto_tambien_continua(local, seed, monkeypatch):
    store = with_store(monkeypatch, MemoryStore())
    store.fail_uploads = {3: StoreConflict("manifest")}     # 1-2 = CSV de la semilla, 3 = manifiesto
    datastore.startup()
    assert sorted(p.name for p in local.glob("*.csv")) == sorted(SEED_FILES)


def test_api_con_fallo_solo_en_la_subida_responde_503_y_las_lecturas_no_mienten(bd, data_dir, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    store = with_store(monkeypatch, MemoryStore({"sites.csv": fixture_bytes("sites_valido.csv")}))
    monkeypatch.setenv("DATA_REFRESH_SECONDS", "0")
    datastore.startup()
    client = TestClient(app)
    store.fail_uploads = {1: StoreError("caído")}
    r = upload(client, "sites", "sites_limites.csv", "sites.csv", dry_run=False)
    assert r.status_code == 503 and r.json()["detail"] == c.MSG_BUCKET_UNAVAILABLE
    assert {s["site_id"] for s in client.get("/api/sites").json()} == {"sitio_uno", "sitio_dos", "sitio_tres"}
    from app.db import session as db_session
    from app.db.models import EventoAuditoria
    with db_session.session_scope() as s:
        assert s.query(EventoAuditoria).count() == 0

"""Gestor de claves PQC: pares por usuario, entrega única de la privada, revocar y reemitir, KEM-DEM."""
import base64
import uuid
import warnings

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PublicKey
from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM1024PrivateKey, MLKEM1024PublicKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import config
from app.core.errors import register_handlers
from app.db import session as db_session
from app.db.base import Base
from app.db.enums import AccionAuditoria
from app.db.models import EventoAuditoria, Usuario
from app.models.claves import ClaveUsuario, EstadoClave, TipoClave
from app.repositories import claves as repositorio_claves
from app.repositories.claves import SqlClavesRepository
from app.routers import admin_claves
from app.security import gestor_claves as gk
from app.security import pqc
from app.services.usuarios import crear_usuario
from app.utils import auditoria_util

ADMIN_KEY = "admin-secreto"
HEADERS = {"X-API-Key": ADMIN_KEY}


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    for nombre in (pqc.ENV_CLOUD_RUN_MARKER, pqc.ENV_ENVIRONMENT, config.ENV_KEM_PRIVATE, config.ENV_EMAIL_HASH_KEY):
        monkeypatch.delenv(nombre, raising=False)
    privada, publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, publica)
    monkeypatch.setenv(pqc.ENV_DEV_KEY_DIR, str(tmp_path / "dev"))
    monkeypatch.setenv(config.ENV_ADMIN_API_KEY, ADMIN_KEY)
    monkeypatch.setenv(config.ENV_UPLOAD_API_KEY, "clave-de-carga")
    pqc.reiniciar()
    gk.reiniciar()
    auditoria_util.set_firmante()
    engine = create_engine(f"sqlite:///{tmp_path / 'claves.db'}")
    Base.metadata.create_all(engine)
    db_session.configure(engine)
    yield engine
    db_session.configure(None)
    pqc.reiniciar()
    gk.reiniciar()


@pytest.fixture
def engine(entorno):
    return entorno


@pytest.fixture
def client():
    app = FastAPI()
    register_handlers(app)
    app.include_router(admin_claves.router)
    return TestClient(app)


def _usuario(engine, email="ana@example.org", nombre="Ana Ruiz", activo=True) -> uuid.UUID:
    with Session(engine) as s:
        usuario = crear_usuario(s, email, nombre, activo=activo)
        s.commit()
        return usuario.id


def _generar(client, usuario_id, tipos=None):
    cuerpo = {"usuario_id": str(usuario_id)}
    if tipos:
        cuerpo["tipos"] = tipos
    return client.post("/api/admin/claves", json=cuerpo, headers=HEADERS)


def _por_tipo(respuesta) -> dict:
    return {c["tipo"]: c for c in respuesta.json()["claves"]}


# ------------------------------------------------------------------ criptografía
def test_tamanos_y_algoritmos_de_los_pares():
    kem, firma = gk.generar_par("kem"), gk.generar_par("firma")
    assert (kem.algoritmo, firma.algoritmo) == ("ML-KEM-1024", "ML-DSA-65")
    assert (len(kem.publica), len(firma.publica)) == (1568, 1952)
    assert (kem.tamano_privada, firma.tamano_privada) == (64, 32)
    assert (config.TAMANO_PRIVADA_FIPS["kem"], config.TAMANO_PRIVADA_FIPS["firma"]) == (3168, 4032)
    assert kem.huella == pqc.huella_clave_publica(kem.publica)


def test_tipo_desconocido():
    with pytest.raises(ValueError):
        gk.generar_par("rsa")


def test_encapsular_y_desencapsular_con_la_privada_entregada():
    par = gk.generar_par("kem")
    privada = serialization.load_pem_private_key(bytes(par.privada_pem), None)
    assert isinstance(privada, MLKEM1024PrivateKey)
    secreto, texto = MLKEM1024PublicKey.from_public_bytes(par.publica).encapsulate()
    assert (len(secreto), len(texto)) == (32, 1568)
    assert privada.decapsulate(texto) == secreto


def test_firmar_con_la_privada_entregada_y_verificar_con_la_publica():
    par = gk.generar_par("firma")
    privada = serialization.load_pem_private_key(bytes(par.privada_pem), None)
    firma = privada.sign(b"informe")
    assert len(firma) == 3309
    assert pqc.verificar_con_clave(par.publica, b"informe", firma) is True
    assert pqc.verificar_con_clave(par.publica, b"otro", firma) is False


def test_borrar_pone_a_cero_la_privada():
    par = gk.generar_par("kem")
    assert any(par.privada_pem)
    gk.borrar(par.privada_pem)
    assert not any(par.privada_pem)


def test_kem_dem_ida_y_vuelta_y_manipulacion():
    par = gk.generar_par("kem")
    blob = gk.kem_dem_cifrar(par.publica, b"dato personal", b"ctx")
    assert b"dato personal" not in blob
    assert gk.kem_dem_descifrar(bytes(serialization.load_pem_private_key(bytes(par.privada_pem), None)
                                      .private_bytes_raw()), blob, b"ctx") == b"dato personal"
    privada = serialization.load_pem_private_key(bytes(par.privada_pem), None)
    manipulado = bytearray(blob)
    manipulado[-1] ^= 1
    for malo in (bytes(manipulado), blob[:-1], blob[:10], b"", b"\x01" + blob[1:]):
        with pytest.raises(gk.ErrorGestorClaves):
            gk.kem_dem_descifrar(privada, malo, b"ctx")
    with pytest.raises(gk.ErrorGestorClaves):
        gk.kem_dem_descifrar(privada, blob, b"otro-contexto")
    with pytest.raises(gk.ErrorGestorClaves):
        gk.kem_dem_descifrar(gk.generar_par("kem") and MLKEM1024PrivateKey.generate(), blob, b"ctx")


def test_kem_dem_es_aleatorio():
    par = gk.generar_par("kem")
    assert gk.kem_dem_cifrar(par.publica, b"x") != gk.kem_dem_cifrar(par.publica, b"x")


def test_email_cifrado_y_hash_de_busqueda():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        blob = gk.cifrar_email("Ana@Example.org ")
        assert b"example" not in blob
        assert gk.descifrar_email(blob) == "ana@example.org"
        assert gk.email_hash(" ANA@example.org") == gk.email_hash("ana@example.org")
        assert gk.email_hash("ana@example.org") != gk.email_hash("otra@example.org")
        assert len(gk.email_hash("ana@example.org")) == 64


def test_claves_del_servidor_desde_entorno_y_hash_distinto_con_otra_clave(monkeypatch):
    semilla, clave_hash = gk.generar_claves_servidor()
    monkeypatch.setenv(config.ENV_KEM_PRIVATE, semilla)
    monkeypatch.setenv(config.ENV_EMAIL_HASH_KEY, clave_hash)
    gk.reiniciar()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        primero = gk.email_hash("ana@example.org")
        assert gk.descifrar_email(gk.cifrar_email("ana@example.org")) == "ana@example.org"
    monkeypatch.setenv(config.ENV_EMAIL_HASH_KEY, gk.generar_claves_servidor()[1])
    gk.reiniciar()
    assert gk.email_hash("ana@example.org") != primero


def test_claves_del_servidor_en_produccion_no_se_inventan(monkeypatch, tmp_path):
    monkeypatch.setenv(pqc.ENV_CLOUD_RUN_MARKER, "apogeo-backend")
    gk.reiniciar()
    with pytest.raises(gk.ErrorGestorClaves):
        gk.cifrar_email("ana@example.org")
    assert not (tmp_path / "dev").exists()


@pytest.mark.parametrize("variable,valor", [(config.ENV_KEM_PRIVATE, "no es base64"), (config.ENV_KEM_PRIVATE, "AAAA")])
def test_claves_del_servidor_invalidas(monkeypatch, variable, valor):
    monkeypatch.setenv(variable, valor)
    monkeypatch.setenv(config.ENV_EMAIL_HASH_KEY, gk.generar_claves_servidor()[1])
    gk.reiniciar()
    with pytest.raises(gk.ErrorGestorClaves):
        gk.email_hash("a@b.c")


def test_clave_de_desarrollo_se_reutiliza(tmp_path):
    with pytest.warns(RuntimeWarning, match="DESARROLLO"):
        primero = gk.email_hash("a@b.c")
    gk.reiniciar()
    with pytest.warns(RuntimeWarning):
        assert gk.email_hash("a@b.c") == primero
    assert (tmp_path / "dev" / ".gitignore").read_text() == "*\n"


# ------------------------------------------------------------------ autenticación
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "mala"}, {"X-API-Key": "clave-de-carga"}, {"X-API-Key": "é".encode("utf-8")}])
def test_sin_clave_de_admin_no_se_entra(client, engine, headers):
    usuario = _usuario(engine)
    for respuesta in (client.get("/api/admin/claves", headers=headers),
                      client.post("/api/admin/claves", json={"usuario_id": str(usuario)}, headers=headers)):
        assert respuesta.status_code == 401


def test_sin_admin_api_key_configurada_queda_cerrado(client, monkeypatch):
    monkeypatch.delenv(config.ENV_ADMIN_API_KEY)
    assert client.get("/api/admin/claves", headers=HEADERS).status_code == 401
    assert client.get("/api/admin/claves", headers={"X-API-Key": ""}).status_code == 401


# ------------------------------------------------------------------ generar y entrega única
def test_generar_entrega_ambas_privadas_una_vez(client, engine):
    usuario = _usuario(engine)
    r = _generar(client, usuario)
    assert r.status_code == 201
    assert r.headers["cache-control"] == "no-store"
    assert "no se puede recuperar" in r.json()["aviso"]
    claves = _por_tipo(r)
    assert set(claves) == {"kem", "firma"}
    assert (claves["kem"]["algoritmo"], claves["firma"]["algoritmo"]) == ("ML-KEM-1024", "ML-DSA-65")
    assert (claves["kem"]["tamano_publica"], claves["firma"]["tamano_publica"]) == (1568, 1952)
    for clave in claves.values():
        assert clave["privada_pem"].startswith("-----BEGIN PRIVATE KEY-----")
        assert clave["clave_publica_pem"].startswith("-----BEGIN PUBLIC KEY-----")
        assert clave["estado"] == "activa" and clave["nombre_archivo_privada"].endswith(".privada.pem")
    privada_kem = serialization.load_pem_private_key(claves["kem"]["privada_pem"].encode(), None)
    publica_kem = serialization.load_pem_public_key(claves["kem"]["clave_publica_pem"].encode())
    secreto, texto = publica_kem.encapsulate()
    assert privada_kem.decapsulate(texto) == secreto


def test_la_entrega_se_cuenta_y_la_base_no_guarda_la_privada(client, engine):
    usuario = _usuario(engine)
    r = _generar(client, usuario)
    claves = _por_tipo(r)
    assert all(c["backups"] == 0 for c in claves.values())     # la respuesta aún no se había enviado
    with Session(engine) as s:
        filas = list(s.scalars(select(ClaveUsuario)))
        assert [f.backups for f in filas] == [1, 1]            # la entrega se contó tras enviar la respuesta
        privadas = [base64.b64decode("".join(c["privada_pem"].splitlines()[1:-1])) for c in claves.values()]
        for fila in filas:
            todo = b"".join(v if isinstance(v, bytes) else str(v).encode() for v in
                            (getattr(fila, col.name) for col in ClaveUsuario.__table__.columns))
            for privada in privadas:
                assert privada[-32:] not in todo
    assert {c.name for c in ClaveUsuario.__table__.columns}.isdisjoint({"clave_privada", "privada", "semilla"})


def test_ningun_endpoint_devuelve_la_privada_dos_veces(client, engine):
    usuario = _usuario(engine)
    claves = _por_tipo(_generar(client, usuario))
    clave_id = claves["kem"]["id"]
    respuestas = [client.get("/api/admin/claves", headers=HEADERS),
                  client.get(f"/api/admin/claves/{clave_id}/publica", headers=HEADERS),
                  client.get(f"/api/admin/claves/{clave_id}/publica?formato=pem", headers=HEADERS),
                  client.get(f"/api/admin/claves/{clave_id}/huella", headers=HEADERS)]
    privadas = [c["privada_pem"] for c in claves.values()]
    for r in respuestas:
        assert r.status_code == 200
        assert "PRIVATE" not in r.text and "privada_pem" not in r.text
        assert not any(p in r.text for p in privadas)


def test_las_rutas_no_ofrecen_ninguna_descarga_de_privada():
    app = FastAPI()
    register_handlers(app)
    app.include_router(admin_claves.router)
    rutas = {(m, r.path) for r in app.routes if hasattr(r, "methods") and r.path.startswith("/api/admin/claves")
             for m in r.methods}
    assert not any("privada" in ruta for _, ruta in rutas)
    entregan = {ruta for metodo, ruta in rutas if metodo == "GET"}
    assert entregan == {"/api/admin/claves", "/api/admin/claves/{clave_id}/publica", "/api/admin/claves/{clave_id}/huella"}
    # La privada solo aparece en el esquema de la respuesta de generar y rotar
    esquema = app.openapi()["components"]["schemas"]
    con_privada = {nombre for nombre, s in esquema.items() if "privada_pem" in s.get("properties", {})}
    assert con_privada == {"ClaveEntregada"}


def test_generar_un_solo_tipo(client, engine):
    r = _generar(client, _usuario(engine), ["firma"])
    assert r.status_code == 201 and set(_por_tipo(r)) == {"firma"}


def test_generar_dos_veces_el_mismo_tipo_es_409(client, engine):
    usuario = _usuario(engine)
    assert _generar(client, usuario).status_code == 201
    segunda = _generar(client, usuario, ["kem"])
    assert segunda.status_code == 409 and "rotar" in segunda.json()["detail"]
    with Session(engine) as s:
        assert s.query(ClaveUsuario).count() == 2


def test_usuario_inexistente_o_inactivo(client, engine):
    assert _generar(client, uuid.uuid4()).status_code == 404
    assert _generar(client, _usuario(engine, "b@example.org", activo=False)).status_code == 409


def test_cuerpo_invalido(client):
    assert client.post("/api/admin/claves", json={}, headers=HEADERS).status_code == 422
    assert client.post("/api/admin/claves", json={"usuario_id": "x"}, headers=HEADERS).status_code == 422
    assert client.post("/api/admin/claves", json={"usuario_id": str(uuid.uuid4()), "tipos": ["rsa"]},
                       headers=HEADERS).status_code == 422


def test_una_sola_clave_activa_por_usuario_y_tipo_en_la_base(engine):
    usuario = _usuario(engine)
    with Session(engine) as s:
        for i in range(2):
            par = gk.generar_par("kem")
            s.add(ClaveUsuario(usuario_id=usuario, tipo=TipoClave.KEM, algoritmo=par.algoritmo, clave_publica=par.publica,
                               huella_sha3_256=par.huella, tamano_publica=par.tamano_publica,
                               tamano_privada=par.tamano_privada, estado=EstadoClave.ACTIVA, backups=0))
            if i == 0:
                s.commit()
        with pytest.raises(IntegrityError):
            s.commit()


# ------------------------------------------------------------------ listar
def test_listar_con_q_sin_claves_del_servidor_busca_solo_por_nombre(client, engine, monkeypatch):
    ana, luis = _usuario(engine, "ana@example.org", "Ana Ruiz"), _usuario(engine, "luis@example.org", "Luis Paz")
    _generar(client, ana)
    _generar(client, luis, ["kem"])
    monkeypatch.setenv("K_SERVICE", "apogeo-backend")                    # producción sin PQC_KEM_PRIVATE_KEY ni EMAIL_HASH_KEY
    monkeypatch.delenv(config.ENV_KEM_PRIVATE, raising=False)
    monkeypatch.delenv(config.ENV_EMAIL_HASH_KEY, raising=False)
    gk.reiniciar()
    r = client.get("/api/admin/claves?q=Ruiz", headers=HEADERS)
    assert r.status_code == 200 and r.json()["total"] == 2
    assert all(i["usuario_email"] is None for i in r.json()["items"])     # no se pueden descifrar, pero no hay 500
    assert client.get("/api/admin/claves?q=ana@example.org", headers=HEADERS).json()["total"] == 0   # el email exacto necesita las claves
    assert client.get("/api/admin/claves", headers=HEADERS).status_code == 200


def test_listar_con_filtros_y_paginacion(client, engine):
    ana, luis = _usuario(engine, "ana@example.org", "Ana Ruiz"), _usuario(engine, "luis@example.org", "Luis_Paz")
    _generar(client, ana)
    _generar(client, luis, ["kem"])
    todo = client.get("/api/admin/claves", headers=HEADERS).json()
    assert todo["total"] == 3 and len(todo["items"]) == 3
    item = next(i for i in todo["items"] if i["usuario_email"] == "luis@example.org")
    assert item["tamano_privada_fips"] == 3168 and item["backups"] == 1 and item["usuario_nombre"] == "Luis_Paz"
    assert client.get("/api/admin/claves?tipo=firma", headers=HEADERS).json()["total"] == 1
    assert client.get(f"/api/admin/claves?usuario_id={luis}", headers=HEADERS).json()["total"] == 1
    assert client.get("/api/admin/claves?q=ANA", headers=HEADERS).json()["total"] == 2
    assert client.get("/api/admin/claves?q=Luis_", headers=HEADERS).json()["total"] == 1
    assert client.get("/api/admin/claves?q=%25", headers=HEADERS).json()["total"] == 0        # % no es comodín
    assert client.get("/api/admin/claves?estado=revocada", headers=HEADERS).json()["total"] == 0
    pagina = client.get("/api/admin/claves?limite=2&offset=2", headers=HEADERS).json()
    assert pagina["total"] == 3 and len(pagina["items"]) == 1
    for malo in ("limite=0", "limite=201", "offset=-1", "estado=otra", "tipo=rsa", "usuario_id=x"):
        assert client.get(f"/api/admin/claves?{malo}", headers=HEADERS).status_code == 422


# ------------------------------------------------------------------ pública y huella
def test_publica_y_huella(client, engine):
    clave = _por_tipo(_generar(client, _usuario(engine)))["firma"]
    publica = client.get(f"/api/admin/claves/{clave['id']}/publica", headers=HEADERS).json()
    assert publica["huella_sha3_256"] == clave["huella_sha3_256"]
    crudo = base64.b64decode(publica["clave_publica_b64"])
    assert len(crudo) == 1952 and pqc.huella_clave_publica(crudo) == publica["huella_sha3_256"]
    pem = client.get(f"/api/admin/claves/{clave['id']}/publica?formato=pem", headers=HEADERS)
    assert pem.text == publica["clave_publica_pem"] and "attachment" in pem.headers["content-disposition"]
    huella = client.get(f"/api/admin/claves/{clave['id']}/huella", headers=HEADERS).json()
    assert huella["huella_sha3_256"] == clave["huella_sha3_256"] and huella["estado"] == "activa"
    assert client.get(f"/api/admin/claves/{uuid.uuid4()}/publica", headers=HEADERS).status_code == 404
    assert client.get("/api/admin/claves/no-uuid/huella", headers=HEADERS).status_code == 422
    assert client.get(f"/api/admin/claves/{clave['id']}/publica?formato=xml", headers=HEADERS).status_code == 422


# ------------------------------------------------------------------ revocar y rotar
def test_lo_firmado_antes_sigue_verificable_con_la_publica_revocada(client, engine):
    clave = _por_tipo(_generar(client, _usuario(engine)))["firma"]
    privada = serialization.load_pem_private_key(clave["privada_pem"].encode(), None)
    firma = privada.sign(b"informe firmado antes de revocar")
    r = client.post(f"/api/admin/claves/{clave['id']}/revocar", json={"motivo": "pérdida del dispositivo"}, headers=HEADERS)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estado"] == "revocada" and cuerpo["motivo_revocacion"] == "pérdida del dispositivo"
    assert cuerpo["revocada_utc"] is not None
    publica = client.get(f"/api/admin/claves/{clave['id']}/publica", headers=HEADERS).json()
    assert publica["estado"] == "revocada"
    clave_publica = serialization.load_pem_public_key(publica["clave_publica_pem"].encode())
    assert isinstance(clave_publica, MLDSA65PublicKey)
    clave_publica.verify(firma, b"informe firmado antes de revocar")      # no lanza: sigue siendo verificable
    assert pqc.verificar_con_clave(base64.b64decode(publica["clave_publica_b64"]), b"informe firmado antes de revocar", firma)


def test_revocar_exige_motivo_y_no_se_repite(client, engine):
    clave = _por_tipo(_generar(client, _usuario(engine)))["kem"]
    url = f"/api/admin/claves/{clave['id']}/revocar"
    for malo in ({}, {"motivo": ""}, {"motivo": "ab"}, {"motivo": "x" * 201}):
        assert client.post(url, json=malo, headers=HEADERS).status_code == 422
    assert client.post(url, json={"motivo": "ya no se usa"}, headers=HEADERS).status_code == 200
    assert client.post(url, json={"motivo": "otra vez"}, headers=HEADERS).status_code == 409
    assert client.post(f"/api/admin/claves/{uuid.uuid4()}/revocar", json={"motivo": "xxx"}, headers=HEADERS).status_code == 404


def test_tras_revocar_se_puede_generar_otra(client, engine):
    usuario = _usuario(engine)
    clave = _por_tipo(_generar(client, usuario))["kem"]
    client.post(f"/api/admin/claves/{clave['id']}/revocar", json={"motivo": "pérdida"}, headers=HEADERS)
    nueva = _generar(client, usuario, ["kem"])
    assert nueva.status_code == 201 and _por_tipo(nueva)["kem"]["huella_sha3_256"] != clave["huella_sha3_256"]


def test_rotar_revoca_y_reemite(client, engine):
    usuario = _usuario(engine)
    vieja = _por_tipo(_generar(client, usuario))["kem"]
    r = client.post(f"/api/admin/claves/{vieja['id']}/rotar", headers=HEADERS)
    assert r.status_code == 201
    nueva = r.json()["claves"][0]
    assert nueva["privada_pem"].startswith("-----BEGIN PRIVATE KEY-----") and nueva["privada_pem"] != vieja["privada_pem"]
    assert nueva["tipo"] == "kem" and nueva["huella_sha3_256"] != vieja["huella_sha3_256"] and nueva["estado"] == "activa"
    lista = {i["id"]: i for i in client.get("/api/admin/claves", headers=HEADERS).json()["items"]}
    assert lista[vieja["id"]]["estado"] == "revocada" and lista[vieja["id"]]["reemplazada_por"] == nueva["id"]
    assert lista[vieja["id"]]["motivo_revocacion"] == "rotación"
    assert lista[nueva["id"]]["backups"] == 1
    assert client.post(f"/api/admin/claves/{vieja['id']}/rotar", headers=HEADERS).status_code == 409
    con_motivo = client.post(f"/api/admin/claves/{nueva['id']}/rotar", json={"motivo": "caducidad anual"}, headers=HEADERS)
    assert con_motivo.status_code == 201
    with Session(engine) as s:
        activas = s.query(ClaveUsuario).filter_by(usuario_id=usuario, estado=EstadoClave.ACTIVA).count()
        assert activas == 2                                           # una kem y una firma


def test_rotar_con_motivo_invalido(client, engine):
    clave = _por_tipo(_generar(client, _usuario(engine)))["kem"]
    assert client.post(f"/api/admin/claves/{clave['id']}/rotar", json={"motivo": "x"}, headers=HEADERS).status_code == 422


# ------------------------------------------------------------------ auditoría
def test_cada_accion_queda_en_la_auditoria_firmada(client, engine):
    usuario = _usuario(engine)
    claves = _por_tipo(_generar(client, usuario))
    client.post(f"/api/admin/claves/{claves['firma']['id']}/revocar", json={"motivo": "pérdida"}, headers=HEADERS)
    nueva = client.post(f"/api/admin/claves/{claves['kem']['id']}/rotar", headers=HEADERS).json()["claves"][0]
    with Session(engine) as s:
        eventos = list(s.scalars(select(EventoAuditoria).order_by(EventoAuditoria.secuencia)))
    operaciones = [e.detalle["operacion"] for e in eventos]
    assert operaciones == ["clave_generada", "clave_generada", "clave_revocada", "clave_rotada"]
    assert all(e.accion == AccionAuditoria.CAMBIO_DATOS and e.actor == "admin-api-key" for e in eventos)
    assert all(e.algoritmo_firma == "ML-DSA-65" and e.firma_pqc for e in eventos)
    assert eventos[2].detalle["motivo"] == "pérdida" and eventos[3].detalle["reemplaza_a"] == claves["kem"]["id"]
    assert eventos[3].recurso == f"clave/{nueva['id']}"
    resultado = auditoria_util.verificar_cadena()
    assert resultado.valida and resultado.n_eventos == 4
    privadas = [c["privada_pem"] for c in claves.values()] + [nueva["privada_pem"]]
    for evento in eventos:                                           # la privada no llega a la auditoría
        assert not any(p in str(evento.detalle) or "PRIVATE" in str(evento.detalle) for p in privadas)


def test_si_la_auditoria_falla_no_se_hace_nada(client, engine, monkeypatch):
    usuario = _usuario(engine)

    def falla(*a, **k):
        raise auditoria_util.ErrorAuditoria("fallo")

    monkeypatch.setattr(auditoria_util, "registrar_evento", falla)
    r = _generar(client, usuario)
    assert r.status_code == 503 and "PRIVATE" not in r.text
    with Session(engine) as s:
        assert s.query(ClaveUsuario).count() == 0


def test_si_la_auditoria_falla_al_revocar_o_rotar_no_cambia_nada(client, engine, monkeypatch):
    clave = _por_tipo(_generar(client, _usuario(engine)))["kem"]

    def falla(*a, **k):
        raise auditoria_util.ErrorAuditoria("fallo")

    monkeypatch.setattr(auditoria_util, "registrar_evento", falla)
    assert client.post(f"/api/admin/claves/{clave['id']}/revocar", json={"motivo": "pérdida"}, headers=HEADERS).status_code == 503
    assert client.post(f"/api/admin/claves/{clave['id']}/rotar", headers=HEADERS).status_code == 503
    with Session(engine) as s:
        filas = list(s.scalars(select(ClaveUsuario)))
        assert len(filas) == 2 and all(f.estado == EstadoClave.ACTIVA for f in filas)


# ------------------------------------------------------------------ migración
def test_la_migracion_coincide_con_el_modelo(tmp_path):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import inspect
    raiz = __import__("pathlib").Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    assert "claves_usuario" in inspect(eng).get_table_names()
    with eng.connect() as conn:
        diferencias = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diferencias == [], diferencias
    command.downgrade(cfg, "0001")
    assert "claves_usuario" not in inspect(create_engine(url)).get_table_names()
    command.downgrade(cfg, "base")


# ------------------------------------------------------------------ script de claves del servidor
def test_script_genera_las_claves_del_servidor(monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    entorno = {k: v for k, v in __import__("os").environ.items() if not k.startswith(("PQC_", "EMAIL_"))}
    res = subprocess.run([sys.executable, str(raiz / "scripts" / "pqc" / "generar_claves.py"), "--servidor"],
                         capture_output=True, text=True, env=entorno, cwd=raiz, timeout=60)
    assert res.returncode == 0, res.stderr
    valores = dict(linea.split("=", 1) for linea in res.stdout.splitlines())
    assert set(valores) == {config.ENV_KEM_PRIVATE, config.ENV_EMAIL_HASH_KEY}
    assert valores[config.ENV_KEM_PRIVATE] not in res.stderr
    monkeypatch.setenv(config.ENV_KEM_PRIVATE, valores[config.ENV_KEM_PRIVATE])
    monkeypatch.setenv(config.ENV_EMAIL_HASH_KEY, valores[config.ENV_EMAIL_HASH_KEY])
    gk.reiniciar()
    assert gk.descifrar_email(gk.cifrar_email("ana@example.org")) == "ana@example.org"


# ------------------------------------------------------------------ rotación de claves del servidor
def _configurar(monkeypatch, semilla, clave_hash, previas="", hash_previas=""):
    monkeypatch.setenv(config.ENV_KEM_PRIVATE, semilla)
    monkeypatch.setenv(config.ENV_EMAIL_HASH_KEY, clave_hash)
    for nombre, valor in ((config.ENV_KEM_PREVIOUS, previas), (config.ENV_EMAIL_HASH_PREVIOUS, hash_previas)):
        monkeypatch.setenv(nombre, valor) if valor else monkeypatch.delenv(nombre, raising=False)
    gk.reiniciar()


def test_el_blob_lleva_el_id_de_la_clave_que_lo_cifro():
    par = gk.generar_par("kem")
    blob = gk.kem_dem_cifrar(par.publica, b"x")
    assert gk.id_del_blob(blob) == gk.id_de_clave(par.publica) and len(gk.id_de_clave(par.publica)) == 8
    assert len(blob) == config.KEM_DEM_HEADER_BYTES + 1 + 16


def test_rotar_la_clave_kem_sin_perder_los_emails_ya_cifrados(monkeypatch):
    vieja_kem, vieja_hash = gk.generar_claves_servidor()
    nueva_kem, nueva_hash = gk.generar_claves_servidor()
    _configurar(monkeypatch, vieja_kem, vieja_hash)
    antiguo = gk.cifrar_email("ana@example.org")
    assert not gk.necesita_reencriptar(antiguo)
    # Rotación: la nueva es la actual y la vieja pasa a «anteriores»
    _configurar(monkeypatch, nueva_kem, vieja_hash, previas=vieja_kem)
    assert gk.descifrar_email(antiguo) == "ana@example.org"          # sigue accesible
    assert gk.necesita_reencriptar(antiguo)
    nuevo = gk.reencriptar_email(antiguo)
    assert not gk.necesita_reencriptar(nuevo) and gk.id_del_blob(nuevo) != gk.id_del_blob(antiguo)
    assert gk.descifrar_email(nuevo) == "ana@example.org"
    assert gk.id_del_blob(gk.cifrar_email("otra@example.org")) == gk.id_del_blob(nuevo)   # cifra con la actual
    # Cuando ya no queda nada cifrado con la vieja, se retira de «anteriores»
    _configurar(monkeypatch, nueva_kem, vieja_hash)
    assert gk.descifrar_email(nuevo) == "ana@example.org"
    with pytest.raises(gk.ErrorGestorClaves, match="ya no está configurada"):
        gk.descifrar_email(antiguo)


def test_rotar_la_clave_de_hash_con_busqueda_en_la_transicion(monkeypatch):
    kem, vieja = gk.generar_claves_servidor()
    _, nueva = gk.generar_claves_servidor()
    _configurar(monkeypatch, kem, vieja)
    guardado = gk.email_hash("ana@example.org")
    _configurar(monkeypatch, kem, nueva, hash_previas=vieja)
    candidatos = gk.email_hash_candidatos(" ANA@example.org")
    assert candidatos[0] == gk.email_hash("ana@example.org") != guardado     # la actual va primero
    assert guardado in candidatos and len(candidatos) == 2                   # la búsqueda aún encuentra el viejo
    _configurar(monkeypatch, kem, nueva)
    assert guardado not in gk.email_hash_candidatos("ana@example.org")


@pytest.mark.parametrize("variable", [config.ENV_KEM_PREVIOUS, config.ENV_EMAIL_HASH_PREVIOUS])
def test_claves_anteriores_invalidas(monkeypatch, variable):
    semilla, clave_hash = gk.generar_claves_servidor()
    _configurar(monkeypatch, semilla, clave_hash)
    monkeypatch.setenv(variable, "no es base64,AAAA")
    gk.reiniciar()
    with pytest.raises(gk.ErrorGestorClaves):
        gk.email_hash("a@b.c")


# ------------------------------------------------------------------ correcciones de la revisión del PR
def test_admin_api_key_igual_a_la_de_carga_deja_el_gestor_cerrado(client, monkeypatch):
    monkeypatch.setenv(config.ENV_UPLOAD_API_KEY, ADMIN_KEY)         # la misma clave para las dos cosas
    assert client.get("/api/admin/claves", headers=HEADERS).status_code == 401


def test_la_comparacion_de_claves_se_reutiliza_de_core_security():
    from app.core.security import matches_api_key
    assert matches_api_key("clave", "clave") and not matches_api_key("otra", "clave")
    assert not matches_api_key("clave", "") and not matches_api_key(None, "clave") and not matches_api_key("", "")
    assert matches_api_key("é".encode("utf-8").decode("latin-1"), "é")      # cabecera UTF-8 leída como latin-1


def test_carrera_en_generar_da_409_y_no_500(client, engine, monkeypatch):
    usuario = _usuario(engine)

    original = Session.flush

    def choque(self, *a, **k):
        if any(isinstance(o, ClaveUsuario) for o in self.new):
            raise IntegrityError("INSERT", {}, Exception("índice único: ya hay una activa"))
        return original(self, *a, **k)

    with monkeypatch.context() as parche:
        parche.setattr(Session, "flush", choque)
        r = _generar(client, usuario)
    assert r.status_code == 409 and "PRIVATE" not in r.text
    with Session(engine) as s:
        assert s.query(ClaveUsuario).count() == 0


def test_carrera_en_rotar_da_409_y_no_cambia_la_clave(client, engine, monkeypatch):
    clave = _por_tipo(_generar(client, _usuario(engine)))["kem"]
    original = Session.flush

    def choque_al_insertar(self, *a, **k):
        if any(isinstance(o, ClaveUsuario) for o in self.new):
            raise IntegrityError("INSERT", {}, Exception("índice único"))
        return original(self, *a, **k)

    with monkeypatch.context() as parche:
        parche.setattr(Session, "flush", choque_al_insertar)
        r = client.post(f"/api/admin/claves/{clave['id']}/rotar", headers=HEADERS)
    assert r.status_code == 409
    estados = {i["id"]: i["estado"] for i in client.get("/api/admin/claves", headers=HEADERS).json()["items"]}
    assert estados[clave["id"]] == "activa"


def test_revocar_y_rotar_piden_bloqueo_de_fila(engine, monkeypatch):
    pedidos = []
    original = Session.scalar

    def espia(self, statement, *a, **k):
        pedidos.append(getattr(statement, "_for_update_arg", None) is not None)
        return original(self, statement, *a, **k)

    monkeypatch.setattr(Session, "scalar", espia)
    with Session(engine) as s:
        SqlClavesRepository(s).obtener(uuid.uuid4(), bloquear=True)
        SqlClavesRepository(s).obtener(uuid.uuid4())
    assert pedidos == [True, False]


def test_las_lecturas_dejan_rastro_en_el_log_sin_emails(client, engine, caplog):
    usuario = _usuario(engine, "secreta@example.org", "Persona Secreta")
    clave = _por_tipo(_generar(client, usuario))["kem"]
    with caplog.at_level("INFO", logger=config.KEYS_LOGGER):
        client.get("/api/admin/claves?q=secreta", headers=HEADERS)
        client.get(f"/api/admin/claves/{clave['id']}/publica", headers=HEADERS)
        client.get(f"/api/admin/claves/{clave['id']}/huella", headers=HEADERS)
    texto = "\n".join(r.getMessage() for r in caplog.records)
    for operacion in ("lectura:listar", "lectura:publica", "lectura:huella"):
        assert operacion in texto
    assert "secreta@example.org" not in texto and "Persona Secreta" not in texto and "secreta" not in texto.replace("lectura:", "")


def test_si_falla_el_contador_la_entrega_sigue_y_backups_queda_en_0(client, engine, monkeypatch):
    class _SinBase:
        @staticmethod
        def session_scope():
            raise RuntimeError("base caída solo para el contador")

    with monkeypatch.context() as parche:
        parche.setattr(repositorio_claves, "db_session", _SinBase)
        r = _generar(client, _usuario(engine))
    assert r.status_code == 201 and r.json()["claves"][0]["privada_pem"].startswith("-----BEGIN PRIVATE KEY-----")
    with Session(engine) as s:
        assert [k.backups for k in s.scalars(select(ClaveUsuario))] == [0, 0]

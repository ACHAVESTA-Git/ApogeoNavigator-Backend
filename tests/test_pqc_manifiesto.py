"""Pruebas del manifiesto firmado de data/ y de scripts/pqc/{generar,verificar}_manifiesto.py."""
import base64
import copy
import functools
import http.server
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.security import pqc

BACKEND_DIR = Path(__file__).resolve().parents[1]
VERIFICAR = BACKEND_DIR / "scripts" / "pqc" / "verificar_manifiesto.py"
GENERAR = BACKEND_DIR / "scripts" / "pqc" / "generar_manifiesto.py"
AHORA = datetime(2026, 11, 15, 12, 0, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def claves(monkeypatch, tmp_path):
    for nombre in (pqc.ENV_CLOUD_RUN_MARKER, pqc.ENV_ENVIRONMENT):
        monkeypatch.delenv(nombre, raising=False)
    privada, publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, publica)
    monkeypatch.setenv(pqc.ENV_DEV_KEY_DIR, str(tmp_path / "pqc_dev"))
    pqc.reiniciar()
    yield publica
    pqc.reiniciar()


@pytest.fixture
def datos(tmp_path) -> Path:
    carpeta = tmp_path / "data"
    (carpeta / "_historial").mkdir(parents=True)
    (carpeta / "sites.csv").write_bytes(b"site_id,nombre\nshackleton,Shackleton\n")
    (carpeta / "efemerides_shackleton.csv").write_bytes(b"utc,sol_el_deg\n2026-12-01T00:00Z,1.1\n")
    (carpeta / "metricas.csv").write_bytes("site_id,notas\nshackleton,ñandú\n".encode())
    (carpeta / "_historial" / "sites_viejo.csv").write_bytes(b"no entra")
    (carpeta / "notas.txt").write_bytes(b"no es csv")
    return carpeta


@pytest.fixture
def manifiesto(datos) -> dict:
    return pqc.generar_manifiesto(datos, ahora=AHORA)


def _verificar(manifiesto, datos=None, clave=None):
    return pqc.verificar_manifiesto(manifiesto, datos, clave)


# ------------------------------------------------------------------ generación
def test_contenido_del_manifiesto(manifiesto):
    assert manifiesto["version"] == 1 and manifiesto["algoritmo"] == "ML-DSA-65"
    assert manifiesto["generado_utc"] == "2026-11-15T12:00:05Z"
    assert manifiesto["huella_clave_publica"] == pqc.huella_clave_publica()
    assert [a["nombre"] for a in manifiesto["archivos"]] == [
        "efemerides_shackleton.csv", "metricas.csv", "sites.csv"]
    assert len(base64.b64decode(manifiesto["firma_b64"])) == pqc.SIGNATURE_BYTES
    assert all(set(a) == {"nombre", "bytes", "sha3_256"} for a in manifiesto["archivos"])


def test_sin_historial_ni_otros_archivos(manifiesto):
    nombres = {a["nombre"] for a in manifiesto["archivos"]}
    assert "sites_viejo.csv" not in nombres and "notas.txt" not in nombres


def test_hash_y_tamano_correctos(manifiesto, datos):
    import hashlib
    entrada = next(a for a in manifiesto["archivos"] if a["nombre"] == "metricas.csv")
    contenido = (datos / "metricas.csv").read_bytes()
    assert entrada["bytes"] == len(contenido)
    assert entrada["sha3_256"] == hashlib.sha3_256(contenido).hexdigest()


def test_es_verificable_y_reproducible_en_formato(manifiesto, datos):
    assert _verificar(manifiesto, datos) == []
    otra = pqc.generar_manifiesto(datos, ahora=AHORA)
    assert otra["archivos"] == manifiesto["archivos"]


def test_canonicalizar_es_determinista():
    a = pqc.canonicalizar({"b": 1, "a": [1, "ñ"]})
    assert a == pqc.canonicalizar({"a": [1, "ñ"], "b": 1}) == '{"a":[1,"ñ"],"b":1}'.encode()


def test_contenidos_nuevos_se_firman_antes_de_guardar(datos):
    nuevo = b"site_id,nombre\notro,Otro\n"
    m = pqc.generar_manifiesto(datos, contenidos_nuevos={"sites.csv": nuevo}, ahora=AHORA)
    (datos / "sites.csv").write_bytes(nuevo)
    assert _verificar(m, datos) == []


@pytest.mark.parametrize("nombre", ["../x.csv", "sub/x.csv", "x.txt", ""])
def test_contenidos_nuevos_con_nombre_invalido(datos, nombre):
    with pytest.raises(ValueError):
        pqc.generar_manifiesto(datos, contenidos_nuevos={nombre: b"x"})


def test_escritura_atomica(datos, manifiesto):
    ruta = pqc.escribir_manifiesto(datos, manifiesto)
    assert ruta == datos / "manifest.json"
    assert json.loads(ruta.read_text(encoding="utf-8")) == manifiesto
    assert oct(ruta.stat().st_mode & 0o777) == oct(pqc.MANIFEST_FILE_MODE)
    assert not list(datos.glob("*.tmp"))


# ------------------------------------------------------------------ verificación: detecta manipulación
def test_csv_modificado(manifiesto, datos):
    (datos / "sites.csv").write_bytes(b"site_id,nombre\nshackleton,Otro\n")
    problemas = _verificar(manifiesto, datos)
    assert any("sites.csv" in p and "SHA3-256" in p for p in problemas)


def test_csv_con_mismo_tamano_pero_distinto(manifiesto, datos):
    original = (datos / "metricas.csv").read_bytes()
    (datos / "metricas.csv").write_bytes(original[:-2] + b"XX")
    assert any("metricas.csv" in p for p in _verificar(manifiesto, datos))


def test_csv_faltante_y_csv_sobrante(manifiesto, datos):
    (datos / "sites.csv").unlink()
    (datos / "extra.csv").write_bytes(b"a\n")
    problemas = _verificar(manifiesto, datos)
    assert any("sites.csv" in p and "no existe" in p for p in problemas)
    assert any("extra.csv" in p and "no figura" in p for p in problemas)


def test_manifiesto_alterado(manifiesto, datos):
    for cambio in (lambda m: m["archivos"][0].update(bytes=1),
                   lambda m: m["archivos"][0].update(sha3_256="0" * 64),
                   lambda m: m.update(generado_utc="2027-01-01T00:00:00Z"),
                   lambda m: m["archivos"].pop()):
        copia = copy.deepcopy(manifiesto)
        cambio(copia)
        assert any("firma" in p.lower() for p in _verificar(copia, datos)), cambio


def test_firma_alterada(manifiesto, datos):
    firma = bytearray(base64.b64decode(manifiesto["firma_b64"]))
    firma[5] ^= 1
    copia = {**manifiesto, "firma_b64": base64.b64encode(bytes(firma)).decode()}
    assert any("NO es válida" in p for p in _verificar(copia, datos))


def test_clave_incorrecta(manifiesto, datos):
    _, otra = pqc.generar_par()
    problemas = _verificar(manifiesto, datos, base64.b64decode(otra))
    assert any("huella" in p.lower() for p in problemas) and any("NO es válida" in p for p in problemas)


def test_firma_con_otro_contexto_no_vale_como_manifiesto(datos):
    m = pqc.generar_manifiesto(datos, ahora=AHORA)
    sin_firma = {k: v for k, v in m.items() if k != "firma_b64"}
    m["firma_b64"] = base64.b64encode(pqc.firmar(pqc.canonicalizar(sin_firma), contexto=b"auditoria")).decode()
    assert any("NO es válida" in p for p in _verificar(m, datos))


@pytest.mark.parametrize("roto", [
    {}, {"version": 2, "algoritmo": "ML-DSA-65"}, {"version": 1, "algoritmo": "RSA"},
    {"version": 1, "algoritmo": "ML-DSA-65"},
])
def test_manifiestos_mal_formados(roto):
    problemas = _verificar(roto)
    assert problemas and all(isinstance(p, str) for p in problemas)


def test_firma_no_base64(manifiesto):
    assert _verificar({**manifiesto, "firma_b64": "###"})


# ------------------------------------------------------------------ scripts
def _script(script, *args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PQC_")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                          env=env, cwd=BACKEND_DIR, timeout=60)


def _generar_con_script(datos, claves_env):
    privada, publica = claves_env
    res = _script(GENERAR, "--datos", str(datos), env_extra={pqc.ENV_PRIVATE_KEY: privada})
    assert res.returncode == 0, res.stderr
    return datos / "manifest.json"


@pytest.fixture
def par_env(monkeypatch):
    privada, publica = pqc.generar_par()
    return privada, publica


def test_script_generar_y_verificar_de_extremo_a_extremo(datos, par_env, tmp_path):
    ruta = _generar_con_script(datos, par_env)
    huella = pqc.huella_clave_publica(base64.b64decode(par_env[1]))
    res = _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", par_env[1],
                  "--huella", huella, "--datos", str(datos))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "CORRECTO" in res.stdout and "3 archivos" in res.stdout


def test_script_verificar_detecta_csv_alterado(datos, par_env):
    ruta = _generar_con_script(datos, par_env)
    huella = pqc.huella_clave_publica(base64.b64decode(par_env[1]))
    (datos / "sites.csv").write_bytes(b"manipulado")
    res = _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", par_env[1],
                  "--huella", huella, "--datos", str(datos))
    assert res.returncode == 1 and "sites.csv" in res.stdout and "FALLO" in res.stdout


def test_script_verificar_huella_incorrecta(datos, par_env):
    ruta = _generar_con_script(datos, par_env)
    res = _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", par_env[1],
                  "--huella", "0" * 64, "--datos", str(datos))
    assert res.returncode == 1 and "huella" in res.stdout.lower()


def test_script_verificar_exige_huella_o_sin_huella(datos, par_env):
    ruta = _generar_con_script(datos, par_env)
    res = _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", par_env[1])
    assert res.returncode == 2 and "--huella" in res.stderr
    res = _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", par_env[1], "--sin-huella")
    assert res.returncode == 0 and "AVISO" in res.stdout and "NO comprobados" in res.stdout


def test_script_verificar_entradas_incorrectas(datos, par_env, tmp_path):
    ruta = _generar_con_script(datos, par_env)
    huella = pqc.huella_clave_publica(base64.b64decode(par_env[1]))
    assert _script(VERIFICAR, "--manifiesto", str(tmp_path / "no_existe.json"),
                   "--clave-publica", par_env[1], "--huella", huella).returncode == 2
    assert _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", "no-es-base64!",
                   "--huella", huella).returncode == 2
    assert _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", par_env[1],
                   "--huella", huella, "--datos", str(tmp_path / "nada")).returncode == 2


def test_script_verificar_acepta_clave_en_archivo_json_y_por_url(datos, par_env, tmp_path):
    ruta = _generar_con_script(datos, par_env)
    huella = pqc.huella_clave_publica(base64.b64decode(par_env[1]))
    json_clave = tmp_path / "clave.json"
    json_clave.write_text(json.dumps({"algoritmo": "ML-DSA-65", "clave_publica_b64": par_env[1]}))
    res = _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", str(json_clave), "--huella", huella)
    assert res.returncode == 0, res.stderr
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{servidor.server_address[1]}/clave.json"
        res = _script(VERIFICAR, "--manifiesto", str(ruta), "--clave-publica", url, "--huella", huella)
        assert res.returncode == 0, res.stderr
    finally:
        servidor.shutdown()


def test_script_independiente_no_importa_codigo_del_proyecto():
    texto = VERIFICAR.read_text(encoding="utf-8")
    assert "from app" not in texto and "import app" not in texto


def test_script_y_modulo_acuerdan_el_formato(datos, par_env, monkeypatch):
    """El verificador independiente valida un manifiesto hecho por el módulo del proyecto."""
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, par_env[0])
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, par_env[1])
    pqc.reiniciar()
    pqc.escribir_manifiesto(datos, pqc.generar_manifiesto(datos, ahora=AHORA))
    res = _script(VERIFICAR, "--manifiesto", str(datos / "manifest.json"),
                  "--clave-publica", par_env[1], "--huella", pqc.huella_clave_publica(),
                  "--datos", str(datos))
    assert res.returncode == 0, res.stdout + res.stderr


def test_generar_manifiesto_carpeta_inexistente(tmp_path):
    res = _script(GENERAR, "--datos", str(tmp_path / "nada"))
    assert res.returncode == 1

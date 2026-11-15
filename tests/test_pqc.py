"""Pruebas de app/security/pqc.py (ML-DSA-65, FIPS 204) y de scripts/pqc/generar_claves.py."""
import base64
import os
import stat
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest

from app.security import pqc

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_DIR / "scripts" / "pqc" / "generar_claves.py"
EVENTOS_RENDIMIENTO = 1000
LIMITE_FIRMA_SEG = 60.0
LIMITE_VERIFICACION_SEG = 60.0
EVENTO = b'{"evento":"carga_csv","usuario":"analista","tipo":"sites","sha3":"abc"}'


@pytest.fixture(autouse=True)
def entorno_limpio(tmp_path, monkeypatch):
    """Sin claves en el entorno, carpeta de desarrollo temporal y estado reiniciado."""
    for nombre in (pqc.ENV_PRIVATE_KEY, pqc.ENV_PUBLIC_KEY, pqc.ENV_CLOUD_RUN_MARKER, pqc.ENV_ENVIRONMENT):
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setenv(pqc.ENV_DEV_KEY_DIR, str(tmp_path / "pqc_dev"))
    pqc.reiniciar()
    yield
    pqc.reiniciar()


@pytest.fixture
def claves_env(monkeypatch):
    privada, publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, publica)
    pqc.reiniciar()
    return privada, publica


def _sin_aviso_dev():
    return pytest.warns(RuntimeWarning, match="DESARROLLO")


# ------------------------------------------------------------------ básicas
def test_algoritmo_y_tamanos(claves_env):
    firma = pqc.firmar(EVENTO)
    assert pqc.ALGORITMO == "ML-DSA-65"
    assert len(firma) == pqc.SIGNATURE_BYTES == 3309
    assert len(pqc.clave_publica()) == pqc.PUBLIC_KEY_BYTES == 1952


def test_firmar_y_verificar(claves_env):
    assert pqc.verificar(EVENTO, pqc.firmar(EVENTO)) is True


def test_datos_vacios_se_pueden_firmar(claves_env):
    assert pqc.verificar(b"", pqc.firmar(b"")) is True


def test_firma_alterada_no_valida(claves_env):
    firma = bytearray(pqc.firmar(EVENTO))
    firma[10] ^= 0x01
    assert pqc.verificar(EVENTO, bytes(firma)) is False


@pytest.mark.parametrize("recorte", [1, 100, pqc.SIGNATURE_BYTES])
def test_firma_truncada_o_vacia_no_valida(claves_env, recorte):
    firma = pqc.firmar(EVENTO)
    assert pqc.verificar(EVENTO, firma[:-recorte]) is False


def test_firma_con_bytes_extra_no_valida(claves_env):
    assert pqc.verificar(EVENTO, pqc.firmar(EVENTO) + b"\x00") is False


def test_datos_alterados_no_validan(claves_env):
    firma = pqc.firmar(EVENTO)
    assert pqc.verificar(EVENTO + b" ", firma) is False
    assert pqc.verificar(EVENTO.replace(b"analista", b"revisor_"), firma) is False


def test_clave_incorrecta_no_valida(claves_env):
    firma = pqc.firmar(EVENTO)
    _, otra_publica = pqc.generar_par()
    assert pqc.verificar_con_clave(base64.b64decode(otra_publica), EVENTO, firma) is False
    assert pqc.verificar_con_clave(pqc.clave_publica(), EVENTO, firma) is True


def test_contexto_separa_dominios(claves_env):
    firma = pqc.firmar(EVENTO, contexto=b"auditoria")
    assert pqc.verificar(EVENTO, firma, contexto=b"auditoria") is True
    assert pqc.verificar(EVENTO, firma, contexto=b"carga-csv") is False
    assert pqc.verificar(EVENTO, firma) is False


def test_contexto_demasiado_largo(claves_env):
    with pytest.raises(ValueError):
        pqc.firmar(EVENTO, contexto=b"x" * (pqc.CONTEXT_MAX_BYTES + 1))


def test_firmas_son_validas_aunque_sean_distintas(claves_env):
    a, b = pqc.firmar(EVENTO), pqc.firmar(EVENTO)
    assert pqc.verificar(EVENTO, a) and pqc.verificar(EVENTO, b)


@pytest.mark.parametrize("malo", ["texto", 123, None])
def test_tipos_invalidos(claves_env, malo):
    with pytest.raises(TypeError):
        pqc.firmar(malo)
    with pytest.raises(TypeError):
        pqc.verificar(malo, b"x")
    with pytest.raises(TypeError):
        pqc.verificar(EVENTO, malo)


def test_clave_publica_externa_con_tamano_incorrecto(claves_env):
    with pytest.raises(ValueError):
        pqc.verificar_con_clave(b"corta", EVENTO, pqc.firmar(EVENTO))


def test_huella_es_sha3_256_hex(claves_env):
    huella = pqc.huella_clave_publica()
    assert len(huella) == 64 and int(huella, 16) >= 0
    assert huella == pqc.huella_clave_publica(pqc.clave_publica())


# ------------------------------------------------------------------ carga de claves
def test_claves_desde_entorno(claves_env):
    _, publica = claves_env
    assert pqc.clave_publica() == base64.b64decode(publica)


def test_solo_clave_privada_deriva_la_publica(monkeypatch):
    privada, publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    pqc.reiniciar()
    assert pqc.clave_publica() == base64.b64decode(publica)


def test_solo_clave_publica_verifica_pero_no_firma(monkeypatch):
    privada, publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    pqc.reiniciar()
    firma = pqc.firmar(EVENTO)
    monkeypatch.delenv(pqc.ENV_PRIVATE_KEY)
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, publica)
    pqc.reiniciar()
    assert pqc.verificar(EVENTO, firma) is True
    with pytest.raises(pqc.ErrorClavesPQC):
        pqc.firmar(EVENTO)


def test_par_incoherente_se_rechaza(monkeypatch):
    privada, _ = pqc.generar_par()
    _, otra_publica = pqc.generar_par()
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, privada)
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, otra_publica)
    pqc.reiniciar()
    with pytest.raises(pqc.ErrorClavesPQC):
        pqc.clave_publica()


@pytest.mark.parametrize("variable,valor", [
    (pqc.ENV_PRIVATE_KEY, "no es base64 !!"),
    (pqc.ENV_PRIVATE_KEY, base64.b64encode(b"corta").decode()),
    (pqc.ENV_PUBLIC_KEY, base64.b64encode(b"corta").decode()),
])
def test_claves_invalidas_en_entorno(monkeypatch, variable, valor):
    monkeypatch.setenv(variable, valor)
    pqc.reiniciar()
    with pytest.raises(pqc.ErrorClavesPQC):
        pqc.clave_publica()


def test_misma_semilla_misma_clave_publica(monkeypatch):
    privada, publica = pqc.generar_par()
    assert base64.b64encode(pqc.clave_publica_desde_semilla(privada)).decode() == publica


# ------------------------------------------------------------------ desarrollo y producción
def test_sin_claves_en_desarrollo_genera_par_local_con_aviso(tmp_path):
    with _sin_aviso_dev():
        firma = pqc.firmar(EVENTO)
    carpeta = tmp_path / "pqc_dev"
    archivo = carpeta / pqc.DEV_KEY_FILENAME
    assert pqc.verificar(EVENTO, firma) is True
    assert archivo.is_file()
    assert stat.S_IMODE(archivo.stat().st_mode) == pqc.DEV_FILE_MODE
    assert stat.S_IMODE(carpeta.stat().st_mode) == pqc.DEV_DIR_MODE
    assert (carpeta / pqc.DEV_GITIGNORE_NAME).read_text() == pqc.DEV_GITIGNORE_CONTENT


def test_par_de_desarrollo_se_reutiliza():
    with _sin_aviso_dev():
        primera = pqc.clave_publica()
    pqc.reiniciar()
    with _sin_aviso_dev():
        assert pqc.clave_publica() == primera


def test_archivo_de_desarrollo_corrupto_no_se_sobrescribe(tmp_path):
    carpeta = tmp_path / "pqc_dev"
    carpeta.mkdir()
    (carpeta / pqc.DEV_KEY_FILENAME).write_text("{no es json")
    with pytest.raises(pqc.ErrorClavesPQC):
        pqc.clave_publica()
    assert (carpeta / pqc.DEV_KEY_FILENAME).read_text() == "{no es json"


@pytest.mark.parametrize("variable,valor", [
    (pqc.ENV_CLOUD_RUN_MARKER, "apogeo-backend"),
    (pqc.ENV_ENVIRONMENT, "production"),
    (pqc.ENV_ENVIRONMENT, " Production "),
])
def test_produccion_sin_claves_no_genera_nada(monkeypatch, tmp_path, variable, valor):
    monkeypatch.setenv(variable, valor)
    pqc.reiniciar()
    with pytest.raises(pqc.ErrorClavesPQC):
        pqc.firmar(EVENTO)
    assert not (tmp_path / "pqc_dev").exists()


def test_produccion_con_claves_funciona(monkeypatch, claves_env):
    monkeypatch.setenv(pqc.ENV_CLOUD_RUN_MARKER, "apogeo-backend")
    pqc.reiniciar()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert pqc.verificar(EVENTO, pqc.firmar(EVENTO)) is True


# ------------------------------------------------------------------ interoperabilidad
def test_interopera_con_implementacion_independiente_de_fips_204(claves_env):
    """Una implementación en Python puro del FIPS 204 final valida nuestras firmas y viceversa."""
    ml_dsa = pytest.importorskip("dilithium_py.ml_dsa")
    firma = pqc.firmar(EVENTO)
    assert ml_dsa.ML_DSA_65.verify(pqc.clave_publica(), EVENTO, firma) is True
    publica, secreta = ml_dsa.ML_DSA_65.keygen()
    firma_externa = ml_dsa.ML_DSA_65.sign(secreta, EVENTO)
    assert pqc.verificar_con_clave(publica, EVENTO, firma_externa) is True


# ------------------------------------------------------------------ rendimiento
def test_rendimiento_de_mil_eventos(claves_env, capsys):
    eventos = [EVENTO + str(i).encode() for i in range(EVENTOS_RENDIMIENTO)]
    t0 = time.perf_counter()
    firmas = [pqc.firmar(e) for e in eventos]
    t_firma = time.perf_counter() - t0
    t0 = time.perf_counter()
    assert all(pqc.verificar(e, f) for e, f in zip(eventos, firmas))
    t_verif = time.perf_counter() - t0
    with capsys.disabled():
        print(f"\n[PQC] {EVENTOS_RENDIMIENTO} eventos: firmar {t_firma:.2f} s "
              f"({t_firma / EVENTOS_RENDIMIENTO * 1000:.2f} ms c/u), verificar {t_verif:.2f} s "
              f"({t_verif / EVENTOS_RENDIMIENTO * 1000:.2f} ms c/u)")
    assert t_firma < LIMITE_FIRMA_SEG
    assert t_verif < LIMITE_VERIFICACION_SEG


# ------------------------------------------------------------------ script de claves
def _ejecutar_script(*args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PQC_")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, cwd=BACKEND_DIR, timeout=60)


def test_script_genera_par_utilizable(monkeypatch):
    res = _ejecutar_script()
    assert res.returncode == 0
    lineas = dict(l.split("=", 1) for l in res.stdout.splitlines())
    assert set(lineas) == {pqc.ENV_PRIVATE_KEY, pqc.ENV_PUBLIC_KEY}
    assert "SHA3-256" in res.stderr
    monkeypatch.setenv(pqc.ENV_PRIVATE_KEY, lineas[pqc.ENV_PRIVATE_KEY])
    monkeypatch.setenv(pqc.ENV_PUBLIC_KEY, lineas[pqc.ENV_PUBLIC_KEY])
    pqc.reiniciar()
    assert pqc.verificar(EVENTO, pqc.firmar(EVENTO)) is True
    assert pqc.huella_clave_publica() in res.stderr


def test_script_no_escribe_la_clave_privada_en_stderr():
    res = _ejecutar_script()
    privada = dict(l.split("=", 1) for l in res.stdout.splitlines())[pqc.ENV_PRIVATE_KEY]
    assert privada not in res.stderr


def test_script_solo_publica():
    privada, publica = pqc.generar_par()
    res = _ejecutar_script("--solo-publica", env_extra={pqc.ENV_PRIVATE_KEY: privada})
    assert res.returncode == 0
    assert res.stdout.strip() == f"{pqc.ENV_PUBLIC_KEY}={publica}"


def test_script_solo_publica_sin_clave_privada_falla():
    res = _ejecutar_script("--solo-publica")
    assert res.returncode == 1
    assert pqc.ENV_PRIVATE_KEY in res.stderr

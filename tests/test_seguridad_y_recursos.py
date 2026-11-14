"""Clave no ASCII, subidas grandes sin clave, memoria acotada y guardado robusto."""
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import config
from app.services import storage
from app.utils import csv_reader
from tests.conftest import API_KEY, fixture_bytes, find, report_of, upload

SITES_HEADER = "site_id,nombre,categoria,mision,agencia,lat_deg,lon_este_deg,alt_km,fuente_url,notas\n"


def test_clave_no_ascii_en_la_cabecera_da_401_no_500(client):
    response = client.post("/api/datos/validar/sites", headers={"X-API-Key": "é".encode("latin-1")},
                           files={"file": ("sites.csv", fixture_bytes("sites_valido.csv"))})
    assert response.status_code == 401


def test_clave_configurada_no_ascii_funciona(client, monkeypatch):
    monkeypatch.setenv("UPLOAD_API_KEY", "clavé-ñ")
    response = client.post("/api/datos/validar/sites", headers={"X-API-Key": "clavé-ñ".encode("utf-8")},
                           files={"file": ("sites.csv", fixture_bytes("sites_valido.csv"))})
    assert response.status_code == 200
    assert client.post("/api/datos/validar/sites", headers={"X-API-Key": "clavé-n".encode("utf-8")},
                       files={"file": ("sites.csv", b"x")}).status_code == 401
    assert client.post("/api/datos/validar/sites", headers={"X-API-Key": "é".encode("latin-1")},
                       files={"file": ("sites.csv", b"x")}).status_code == 401


def test_subida_de_30_mb_sin_clave_se_rechaza_antes_de_leer(client):
    response = client.post("/api/datos/sites", files={"file": ("sites.csv", b"a" * 30 * 1024 * 1024)})
    assert response.status_code == 401


def test_subida_de_30_mb_con_clave_da_413_por_content_length(client):
    response = client.post("/api/datos/sites", headers={"X-API-Key": API_KEY},
                           files={"file": ("sites.csv", b"a" * 30 * 1024 * 1024)})
    assert response.status_code == 413


def test_archivo_de_exactamente_5_mb_es_aceptado_y_valido(client):
    fila = "s{i:05d},{nombre},apolo,Apollo,NASA,1,1,,https://nssdc.gsfc.nasa.gov/x,{notas}\n"
    notas = "n" * 500
    vacio = len(fila.format(i=0, nombre="x" * 10, notas=notas)) + 0
    n = (config.MAX_FILE_BYTES - len(SITES_HEADER)) // vacio
    sobra = config.MAX_FILE_BYTES - len(SITES_HEADER) - n * vacio
    assert n <= config.MAX_DATA_ROWS and sobra < n * 100
    filas = [fila.format(i=i, nombre="x" * (10 + (1 if i < sobra else 0)), notas=notas) for i in range(n)]
    raw = (SITES_HEADER + "".join(filas)).encode()
    assert len(raw) == config.MAX_FILE_BYTES
    response = upload(client, "sites", raw, "sites.csv")
    assert response.status_code == 200, response.json()
    assert upload(client, "sites", raw + b"x", "sites.csv").status_code == 413


def test_cabecera_gigante_no_bloquea_ni_agota_memoria(client):
    raw = b"," * (2 * 1024 * 1024) + b"\n" + b"," * (2 * 1024 * 1024) + b"\n"
    t0 = time.monotonic()
    response = upload(client, "sites", raw, "sites.csv")
    assert response.status_code == 422
    assert time.monotonic() - t0 < 20


def test_columna_larga_se_recorta_en_el_informe(client):
    nombre = "c" * 5000
    raw = (SITES_HEADER.strip() + "," + nombre + "\n" + "x," * 10 + "x\n").encode()
    report = report_of(upload(client, "sites", raw, "sites.csv"))
    assert all(len(e["columna"] or "") <= config.MAX_VALUE_CHARS for e in report["errores"])


def test_lector_no_materializa_mas_filas_de_las_necesarias():
    texto = "a\n" + "x\n" * (config.MAX_DATA_ROWS * 10)
    with pytest.raises(Exception) as exc:
        csv_reader.parse_rows(texto)
    assert exc.value.code == "E_TAMANO"


def test_guardados_en_el_mismo_instante_no_pisan_la_copia(client, data_dir):
    for _ in range(3):
        storage.save_atomic("sites.csv", fixture_bytes("sites_valido.csv"))
    assert len(list((data_dir / "_historial").glob("sites_*.csv"))) == 3


def test_permisos_del_archivo_guardado(client, data_dir):
    storage.save_atomic("sites.csv", fixture_bytes("sites_limites.csv"))
    assert stat.S_IMODE(os.stat(data_dir / "sites.csv").st_mode) == config.FILE_MODE


def test_fallo_de_replace_limpia_el_temporal(client, data_dir, monkeypatch):
    def boom(*a, **k):
        raise OSError("disco lleno")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        storage.save_atomic("sites.csv", fixture_bytes("sites_limites.csv"))
    assert not list(data_dir.glob(f"{config.TMP_PREFIX}*"))
    assert (data_dir / "sites.csv").read_bytes() == fixture_bytes("sites_valido.csv")


def test_temporal_no_se_confunde_con_un_csv(client, data_dir):
    storage.save_atomic("efemerides_sitio_uno.csv", fixture_bytes("efemerides_valido.csv"))
    assert not list(data_dir.glob("*.tmp")) and not [p for p in data_dir.glob("*.csv") if p.name.startswith(".")]


def test_cargas_simultaneas_dejan_un_archivo_completo(client, data_dir):
    contenidos = [fixture_bytes("sites_limites.csv"), fixture_bytes("sites_valido.csv"), fixture_bytes("sites_advertencias.csv")]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: storage.save_atomic("sites.csv", contenidos[i % 3]), range(24)))
    assert (data_dir / "sites.csv").read_bytes() in contenidos
    assert len(list((data_dir / "_historial").glob("sites_*.csv"))) == 24
    assert not list(data_dir.glob(f"{config.TMP_PREFIX}*"))


def test_extension_en_mayusculas_se_rechaza_por_nombre(client):
    response = upload(client, "sites", "sites_valido.csv", "sites.CSV")
    assert response.status_code == 422 and find(report_of(response), "E_NOMBRE_ARCHIVO")


def test_nombre_con_salto_de_linea_final_es_E_TIPO_ARCHIVO(client):
    response = upload(client, "sites", "sites_valido.csv", "sites.csv\n")
    assert response.status_code == 415 and find(report_of(response), "E_TIPO_ARCHIVO")

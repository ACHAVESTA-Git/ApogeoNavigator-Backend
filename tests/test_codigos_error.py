"""Al menos una prueba por código de error del estándar (docs/plan/estandar-csv.md §5)."""
import pytest

from tests.conftest import find, fixture_bytes, report_of, upload

# (tipo, fixture, nombre subido, código, HTTP, [(fila, columna), ...] esperados)
CASOS = [
    # ---- archivo
    ("sites", "vacio.csv", "sites.csv", "E_ARCHIVO_VACIO", 422, [(1, None)]),
    ("sites", "solo_cabecera.csv", "sites.csv", "E_ARCHIVO_VACIO", 422, [(1, None)]),
    ("sites", "texto.txt", "sites.txt", "E_TIPO_ARCHIVO", 415, [(1, None)]),
    ("sites", "binario.csv", "sites.csv", "E_TIPO_ARCHIVO", 415, [(1, None)]),
    ("sites", "latin1.csv", "sites.csv", "E_CODIFICACION", 422, [(1, None)]),
    ("sites", "sites_valido.csv", "mis sitios.csv", "E_NOMBRE_ARCHIVO", 422, [(1, None)]),
    ("efemerides", "efemerides_valido.csv", "efemerides.csv", "E_NOMBRE_ARCHIVO", 422, [(1, None)]),
    # ---- cabecera
    ("sites", "cabecera_falta_columna.csv", "sites.csv", "E_CABECERA", 422, [(1, "notas")]),
    ("sites", "cabecera_duplicada.csv", "sites.csv", "E_CABECERA", 422, [(1, "lat_deg")]),
    ("sites", "cabecera_desordenada.csv", "sites.csv", "E_CABECERA", 422, [(1, None)]),
    ("sites", "columna_extra.csv", "sites.csv", "E_COLUMNA_DESCONOCIDA", 422, [(1, "extra")]),
    # ---- filas y celdas
    ("sites", "filas_campos.csv", "sites.csv", "E_NUM_COLUMNAS", 422, [(3, None), (4, None), (5, None)]),
    ("sites", "requerido.csv", "sites.csv", "E_REQUERIDO", 422, [(2, "nombre"), (3, "lat_deg")]),
    ("sites", "tipos.csv", "sites.csv", "E_TIPO", 422,
     [(2, "lat_deg"), (3, "lat_deg"), (4, "lat_deg"), (5, "lat_deg"), (6, "lon_este_deg"), (7, "lon_este_deg"), (8, "lat_deg")]),
    ("sites", "formato.csv", "sites.csv", "E_FORMATO", 422,
     [(2, "site_id"), (3, "site_id"), (4, "fuente_url"), (5, "fuente_url")]),
    ("sites", "longitud.csv", "sites.csv", "E_LONGITUD", 422,
     [(2, "nombre"), (3, "nombre"), (4, "mision"), (5, "notas"), (6, "fuente_url")]),
    ("sites", "rango.csv", "sites.csv", "E_RANGO", 422,
     [(2, "lat_deg"), (3, "lat_deg"), (4, "lon_este_deg"), (5, "lon_este_deg"), (6, "alt_km"), (7, "alt_km")]),
    ("sites", "decimales.csv", "sites.csv", "E_DECIMALES", 422, [(2, "lat_deg")]),
    ("sites", "enum.csv", "sites.csv", "E_ENUM", 422, [(2, "categoria"), (3, "categoria")]),
    ("sites", "caracteres.csv", "sites.csv", "E_CARACTER", 422,
     [(2, "nombre"), (3, "nombre"), (4, "mision"), (5, "agencia"), (6, "notas"), (7, "nombre")]),
    ("sites", "duplicado.csv", "sites.csv", "E_DUPLICADO", 422, [(4, "site_id")]),
    # ---- efemérides
    ("efemerides", "efemerides_formato_zona.csv", "efemerides_sitio_uno.csv", "E_FORMATO", 422, [(6, "utc")]),
    ("efemerides", "efemerides_fecha_invalida.csv", "efemerides_sitio_uno.csv", "E_TIPO", 422, [(5, "utc")]),
    ("efemerides", "efemerides_rango_az.csv", "efemerides_sitio_uno.csv", "E_RANGO", 422,
     [(7, "sol_az_deg"), (8, "sol_az_deg")]),
    ("efemerides", "efemerides_desordenado.csv", "efemerides_sitio_uno.csv", "E_ORDEN", 422, [(13, "utc")]),
    ("efemerides", "efemerides_duplicado.csv", "efemerides_sitio_uno.csv", "E_DUPLICADO", 422, [(12, "utc")]),
    ("efemerides", "efemerides_paso_irregular.csv", "efemerides_sitio_uno.csv", "E_PASO", 422, [(22, "utc"), (23, "utc")]),
    ("efemerides", "efemerides_paso_fuera.csv", "efemerides_sitio_uno.csv", "E_PASO", 422, [(3, "utc")]),
    ("efemerides", "efemerides_salto_fisico.csv", "efemerides_sitio_uno.csv", "E_SALTO_FISICO", 422,
     [(17, "sol_el_deg"), (18, "sol_el_deg")]),
    ("efemerides", "efemerides_pocas_filas.csv", "efemerides_sitio_uno.csv", "E_COHERENCIA", 422, [(2, "utc")]),
    ("efemerides", "efemerides_valido.csv", "efemerides_no_existe.csv", "E_REFERENCIA", 422, [(1, "archivo")]),
    # ---- métricas
    ("metricas", "metricas_referencia.csv", "metricas.csv", "E_REFERENCIA", 422, [(2, "site_id")]),
    ("metricas", "metricas_pct.csv", "metricas.csv", "E_COHERENCIA", 422, [(2, "pct_sol")]),
    ("metricas", "metricas_media_mayor.csv", "metricas.csv", "E_COHERENCIA", 422, [(2, "sol_el_media_deg")]),
    ("metricas", "metricas_media_sin_sol.csv", "metricas.csv", "E_COHERENCIA", 422, [(2, "sol_el_media_deg")]),
    ("metricas", "metricas_horas_rango.csv", "metricas.csv", "E_RANGO", 422, [(2, "horas_sol")]),
    ("metricas", "metricas_fechas.csv", "metricas.csv", "E_COHERENCIA", 422, [(2, "fin_utc"), (3, "fin_utc")]),
    ("metricas", "metricas_duplicado.csv", "metricas.csv", "E_DUPLICADO", 422, [(3, "site_id")]),
]


@pytest.mark.parametrize("tipo,fixture,nombre,codigo,http,esperados", CASOS, ids=[f"{c[3]}:{c[1]}" for c in CASOS])
def test_codigo_de_error(client, tipo, fixture, nombre, codigo, http, esperados):
    response = upload(client, tipo, fixture, nombre)
    assert response.status_code == http
    report = report_of(response)
    assert report["valido"] is False
    assert report["total_errores"] >= len(esperados)
    for fila, columna in esperados:
        assert find(report, codigo, fila, columna), f"falta {codigo} en fila {fila}, columna {columna}: {report['errores']}"


def test_E_TAMANO_por_bytes(client):
    response = upload(client, "sites", b"a" * (5 * 1024 * 1024 + 1), "sites.csv")
    assert response.status_code == 413
    assert find(report_of(response), "E_TAMANO")


def test_E_TAMANO_por_filas(client):
    header = "site_id,nombre,categoria,mision,agencia,lat_deg,lon_este_deg,alt_km,fuente_url,notas\n"
    rows = "".join(f"s{i:05d},Sitio {i},apolo,Apollo,NASA,1,1,,https://nssdc.gsfc.nasa.gov/x,\n" for i in range(10_001))
    response = upload(client, "sites", (header + rows).encode(), "sites.csv")
    assert response.status_code == 413
    assert find(report_of(response), "E_TAMANO")


def test_exactamente_10000_filas_es_valido(client):
    header = "site_id,nombre,categoria,mision,agencia,lat_deg,lon_este_deg,alt_km,fuente_url,notas\n"
    rows = "".join(f"s{i:05d},Sitio {i},apolo,Apollo,NASA,1,1,,https://nssdc.gsfc.nasa.gov/x,\n" for i in range(10_000))
    response = upload(client, "sites", (header + rows).encode(), "sites.csv")
    assert response.status_code == 200
    assert response.json()["total_filas"] == 10_000


def test_10000_filas_mas_linea_final_en_blanco_no_es_E_TAMANO(client):
    header = "site_id,nombre,categoria,mision,agencia,lat_deg,lon_este_deg,alt_km,fuente_url,notas\n"
    rows = "".join(f"s{i:05d},Sitio {i},apolo,Apollo,NASA,1,1,,https://nssdc.gsfc.nasa.gov/x,\n" for i in range(10_000))
    assert upload(client, "sites", (header + rows + "\n").encode(), "sites.csv").status_code == 200


def test_cabecera_ancha_se_reporta_como_columnas_desconocidas(client):
    header = ",".join(f"c{i}" for i in range(21))
    response = upload(client, "sites", (header + "\n" + ",".join("x" * 21) + "\n").encode(), "sites.csv")
    assert response.status_code == 422
    assert find(report_of(response), "E_COLUMNA_DESCONOCIDA", 1, "c20")


def test_solo_bom_es_archivo_vacio(client):
    response = upload(client, "sites", b"\xef\xbb\xbf", "sites.csv")
    assert response.status_code == 422 and find(report_of(response), "E_ARCHIVO_VACIO")


def test_utf16_es_codificacion_no_binario(client):
    response = upload(client, "sites", "site_id,nombre\n1,2\n".encode("utf-16"), "sites.csv")
    assert response.status_code == 422 and find(report_of(response), "E_CODIFICACION")


def test_delimitador_punto_y_coma_se_rechaza(client):
    content = fixture_bytes("sites_valido.csv").replace(b",", b";")
    response = upload(client, "sites", content, "sites.csv")
    assert response.status_code == 422
    assert find(report_of(response), "E_COLUMNA_DESCONOCIDA", 1)


def test_nulo_disfrazado_en_texto_opcional(client):
    header = "site_id,nombre,categoria,mision,agencia,lat_deg,lon_este_deg,alt_km,fuente_url,notas\n"
    row = "sitio_uno,Sitio uno,apolo,Apollo,NASA,1,1,,https://nssdc.gsfc.nasa.gov/x,N/A\n"
    response = upload(client, "sites", (header + row).encode(), "sites.csv")
    assert response.status_code == 422 and find(report_of(response), "E_FORMATO", 2, "notas")


def test_nombre_con_salto_de_linea_final_se_rechaza(client):
    response = upload(client, "sites", "sites_valido.csv", "sites.csv\n")
    assert response.status_code in (415, 422)   # no termina en .csv: se rechaza antes del patrón
    assert not response.json().get("valido", False)


def test_nombre_del_informe_se_sanea(client):
    response = upload(client, "sites", "sites_valido.csv", "si\x07tes.csv")
    assert "\x07" not in report_of(response)["archivo"]


def test_http_status_de_FileError():
    from app.utils.errors import FileError
    assert FileError("E_TAMANO", "x").http_status == 413
    assert FileError("E_TIPO_ARCHIVO", "x").http_status == 415
    assert FileError("E_ARCHIVO_VACIO", "x").http_status == 422


def test_informe_limita_a_100_errores_pero_cuenta_todos(client):
    header = "site_id,nombre,categoria,mision,agencia,lat_deg,lon_este_deg,alt_km,fuente_url,notas\n"
    rows = "".join(f"s{i:04d},Sitio {i},apolo,Apollo,NASA,999,1,,https://nssdc.gsfc.nasa.gov/x,\n" for i in range(150))
    report = report_of(upload(client, "sites", (header + rows).encode(), "sites.csv"))
    assert report["total_errores"] == 150
    assert len(report["errores"]) == 100


def test_valor_se_recorta_a_50_caracteres(client):
    header = "site_id,nombre,categoria,mision,agencia,lat_deg,lon_este_deg,alt_km,fuente_url,notas\n"
    row = f"sitio_uno,{'n' * 121},apolo,Apollo,NASA,1,1,,https://nssdc.gsfc.nasa.gov/x,\n"
    report = report_of(upload(client, "sites", (header + row).encode(), "sites.csv"))
    assert len(report["errores"][0]["valor"]) == 50

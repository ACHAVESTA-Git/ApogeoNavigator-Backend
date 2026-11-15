"""Caso feliz y límites exactos que deben aceptarse."""
from tests.conftest import find, report_of, upload

EF = "efemerides_sitio_uno.csv"


def test_sites_valido(client):
    response = upload(client, "sites", "sites_valido.csv", "sites.csv")
    assert response.status_code == 200
    body = response.json()
    assert body["valido"] is True and body["total_filas"] == 3 and body["errores"] == []


def test_sites_con_bom_y_crlf(client):
    assert upload(client, "sites", "sites_bom_crlf.csv", "sites.csv").status_code == 200


def test_sites_limites_exactos(client):
    """lat ±90, lon ±180, -0, alt -10 y 11, textos de longitud mínima."""
    response = upload(client, "sites", "sites_limites.csv", "sites.csv")
    assert response.status_code == 200, response.json()


def test_sites_acepta_signo_mas_en_numeros(client):
    assert upload(client, "sites", "inyeccion_numerica_valida.csv", "sites.csv").status_code == 200


def test_sites_advertencias_no_bloquean(client):
    response = upload(client, "sites", "sites_advertencias.csv", "sites.csv")
    assert response.status_code == 200
    codes = {w["codigo"] for w in response.json()["advertencias"]}
    assert codes == {"W_COORD_DUPLICADA", "W_ARTEMIS_LATITUD"}


def test_efemerides_valido(client):
    response = upload(client, "efemerides", "efemerides_valido.csv", EF)
    assert response.status_code == 200
    assert response.json()["total_filas"] == 48


def test_efemerides_salto_de_azimut_359_a_0_no_es_error(client):
    assert upload(client, "efemerides", "efemerides_salto_azimut_ok.csv", EF).status_code == 200


def test_metricas_valido(client):
    response = upload(client, "metricas", "metricas_valido.csv", "metricas.csv")
    assert response.status_code == 200, response.json()

"""Seguridad, dry-run, guardado atómico, historial y /api/sites."""
from tests.conftest import fixture_bytes, report_of, upload

EF = "efemerides_sitio_uno.csv"


def test_sin_api_key_401(client):
    assert upload(client, "sites", "sites_valido.csv", "sites.csv", key=None).status_code == 401
    assert upload(client, "sites", "sites_valido.csv", "sites.csv", dry_run=False, key=None).status_code == 401


def test_api_key_incorrecta_401(client):
    assert upload(client, "sites", "sites_valido.csv", "sites.csv", key="otra").status_code == 401


def test_sin_clave_configurada_la_carga_queda_cerrada(client, monkeypatch):
    monkeypatch.delenv("UPLOAD_API_KEY")
    assert upload(client, "sites", "sites_valido.csv", "sites.csv", key="").status_code == 401
    assert upload(client, "sites", "sites_valido.csv", "sites.csv", key="cualquiera").status_code == 401


def test_tipo_desconocido_404(client):
    assert upload(client, "otro", "sites_valido.csv", "sites.csv").status_code == 404


def test_dry_run_no_escribe(client, data_dir):
    antes = (data_dir / "sites.csv").read_bytes()
    content = fixture_bytes("sites_limites.csv")
    assert upload(client, "sites", content, "sites.csv").status_code == 200
    assert (data_dir / "sites.csv").read_bytes() == antes
    assert not (data_dir / "_historial").exists()


def test_guardado_crea_archivo_y_copia_de_historial(client, data_dir):
    nuevo = fixture_bytes("sites_limites.csv")
    response = upload(client, "sites", nuevo, "sites.csv", dry_run=False)
    assert response.status_code == 200
    body = response.json()
    assert body["guardado"] is True and body["copia_historial"].startswith("sites_")
    assert (data_dir / "sites.csv").read_bytes() == nuevo
    copia = data_dir / "_historial" / body["copia_historial"]
    assert copia.read_bytes() == fixture_bytes("sites_valido.csv")
    assert not list(data_dir.glob(".tmp_*"))


def test_guardado_primera_vez_no_hay_historial(client, data_dir):
    response = upload(client, "efemerides", "efemerides_valido.csv", EF, dry_run=False)
    assert response.status_code == 200
    assert response.json()["copia_historial"] is None
    assert (data_dir / EF).is_file()


def test_guardado_invalido_no_toca_los_datos(client, data_dir):
    antes = (data_dir / "sites.csv").read_bytes()
    response = upload(client, "sites", "rango.csv", "sites.csv", dry_run=False)
    assert response.status_code == 422
    assert (data_dir / "sites.csv").read_bytes() == antes
    assert not (data_dir / "_historial").exists() and not list(data_dir.glob(".tmp_*"))


def test_guardado_quita_el_bom(client, data_dir):
    assert upload(client, "sites", "sites_bom_crlf.csv", "sites.csv", dry_run=False).status_code == 200
    assert not (data_dir / "sites.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_nombre_con_ruta_se_rechaza(client, data_dir):
    response = upload(client, "efemerides", "efemerides_valido.csv", "../efemerides_sitio_uno.csv", dry_run=False)
    assert response.status_code == 422
    assert not (data_dir.parent / "efemerides_sitio_uno.csv").exists()


def test_efemerides_sin_sites_csv_da_referencia(client, data_dir):
    (data_dir / "sites.csv").unlink()
    report = report_of(upload(client, "efemerides", "efemerides_valido.csv", EF))
    assert report["errores"][0]["codigo"] == "E_REFERENCIA"


def test_api_sites_numeros_como_numero(client):
    response = client.get("/api/sites")
    assert response.status_code == 200
    sites = response.json()
    assert [s["site_id"] for s in sites] == ["sitio_uno", "sitio_dos", "sitio_tres"]
    assert isinstance(sites[0]["lat_deg"], float) and sites[0]["lat_deg"] == 0.674
    assert sites[0]["alt_km"] == 0.0 and sites[0]["notas"] is None
    assert sites[1]["lon_este_deg"] == 129.0 and sites[1]["notas"] == "Polo sur"


def test_api_sites_503_si_el_archivo_no_cumple(client, data_dir):
    (data_dir / "sites.csv").write_bytes(fixture_bytes("rango.csv"))
    assert client.get("/api/sites").status_code == 503


def test_api_sites_503_si_no_existe(client, data_dir):
    (data_dir / "sites.csv").unlink()
    assert client.get("/api/sites").status_code == 503


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}

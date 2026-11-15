"""Arranque local sin bucket: el manifiesto se firma si falta, y nunca en producción."""
from app.core import config as c
from app.services import datastore


def test_arranque_local_firma_el_manifiesto_si_falta(data_dir):
    (data_dir / c.MANIFEST_NAME).unlink(missing_ok=True)
    datastore.startup()
    assert (data_dir / c.MANIFEST_NAME).exists()


def test_arranque_local_no_pisa_un_manifiesto_existente(data_dir):
    datastore.startup()
    antes = (data_dir / c.MANIFEST_NAME).read_bytes()
    datastore.startup()
    assert (data_dir / c.MANIFEST_NAME).read_bytes() == antes

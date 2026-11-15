"""Servicio de carga con auditoría, datastore y almacén falsos: el guardado atómico y sus errores sin tocar disco ni BD."""
import io
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import config as c
from app.db.enums import AccionAuditoria
from app.security.pqc import ErrorClavesPQC
from app.services.carga import CargaServiceImpl, Revision
from app.services.errores import ErrorServicio

SITES = Path(__file__).parent.parent / "data" / "sites.csv"


class ErrorAuditoriaFalso(Exception):
    pass


class ConflictoFalso(Exception):
    pass


class AuditoriaFalsa:
    ErrorAuditoria = ErrorAuditoriaFalso

    def __init__(self, con_bd=True, falla=False):
        self.con_bd, self.falla, self.llamadas, self.confirmada = con_bd, falla, [], False

    @contextmanager
    def unidad_de_trabajo(self):
        yield object() if self.con_bd else None
        self.confirmada = True

    def registrar_carga(self, tipo, archivo, contenido, filas, valido, errores, accion, **kw):
        if self.falla:
            raise ErrorAuditoriaFalso()
        self.llamadas.append((accion, kw.get("truncado")))
        return SimpleNamespace(carga=SimpleNamespace(ruta_historial=None))


class DatosFalsos:
    DataConflict = ConflictoFalso

    def __init__(self, error=None, copia=None):
        self.error, self.copia, self.guardados = error, copia, []

    def guardar(self, nombre, contenido):
        if self.error:
            raise self.error
        self.guardados.append(nombre)
        return self.copia


class AlmacenFalso:
    @staticmethod
    def build_context():
        return None


def _servicio(**kw):
    auditoria = kw.pop("auditoria", AuditoriaFalsa())
    datos = kw.pop("datos", DatosFalsos())
    return CargaServiceImpl(auditoria, datos, AlmacenFalso()), auditoria, datos


def _revision(servicio, contenido: bytes | None = None, tipo="sites") -> Revision:
    return servicio.revisar(tipo, "sites.csv", io.BytesIO(contenido if contenido is not None else SITES.read_bytes()))


def test_tipo_desconocido_es_404_antes_de_leer_el_archivo():
    servicio, _, _ = _servicio()
    with pytest.raises(ErrorServicio) as e:
        servicio.revisar("otro", "x.csv", io.BytesIO(b""))
    assert e.value.status == c.HTTP_NOT_FOUND


def test_guardar_carga_audita_antes_de_guardar_y_devuelve_la_copia():
    servicio, auditoria, datos = _servicio(datos=DatosFalsos(copia=Path("sites_2026.csv")))
    revision = _revision(servicio)
    assert revision.resultado.report.valido
    salida = servicio.guardar_carga("sites", revision, origen=None)
    assert salida.guardado is True and salida.copia_historial == "sites_2026.csv"
    assert auditoria.llamadas[0][0] == AccionAuditoria.CARGA_GUARDADA and auditoria.confirmada
    assert datos.guardados == [revision.resultado.target_filename]


@pytest.mark.parametrize("error, estado, detalle", [
    (ErrorClavesPQC("sin claves"), c.HTTP_UNAVAILABLE, c.MSG_SIGNING_UNAVAILABLE),
    (ConflictoFalso(), c.HTTP_CONFLICT, c.MSG_DATA_CONFLICT),
])
def test_los_fallos_al_guardar_se_traducen_a_errores_de_servicio(error, estado, detalle):
    servicio, _, _ = _servicio(datos=DatosFalsos(error=error))
    with pytest.raises(ErrorServicio) as e:
        servicio.guardar_carga("sites", _revision(servicio), origen=None)
    assert (e.value.status, e.value.detail) == (estado, detalle)


def test_si_la_auditoria_falla_no_se_guarda_nada():
    servicio, _, datos = _servicio(auditoria=AuditoriaFalsa(falla=True))
    with pytest.raises(ErrorServicio) as e:
        servicio.guardar_carga("sites", _revision(servicio), origen=None)
    assert e.value.detail == c.MSG_AUDIT_UNAVAILABLE and datos.guardados == []


def test_sin_base_de_datos_se_guarda_igual():
    servicio, auditoria, datos = _servicio(auditoria=AuditoriaFalsa(con_bd=False))
    salida = servicio.guardar_carga("sites", _revision(servicio), origen=None)
    assert salida.guardado and auditoria.llamadas == [] and len(datos.guardados) == 1


def test_auditar_un_rechazo_registra_lo_leido_y_el_error_de_auditoria_es_503():
    servicio, auditoria, _ = _servicio()
    revision = _revision(servicio, b"no es un csv valido\n")
    assert not revision.resultado.report.valido
    servicio.auditar("sites", revision, AccionAuditoria.CARGA_RECHAZADA, origen=None)
    assert auditoria.llamadas == [(AccionAuditoria.CARGA_RECHAZADA, False)]
    servicio_roto, _, _ = _servicio(auditoria=AuditoriaFalsa(falla=True))
    with pytest.raises(ErrorServicio) as e:
        servicio_roto.auditar("sites", revision, AccionAuditoria.CARGA_RECHAZADA, origen=None)
    assert e.value.status == c.HTTP_UNAVAILABLE

"""Servicio de claves con repositorio, auditor y gestor falsos: reglas de negocio sin base de datos ni criptografía."""
import uuid
from types import SimpleNamespace

import pytest

from app.core import config as c
from app.db.models import utcnow
from app.models.claves import ClaveUsuario, EstadoClave, TipoClave
from app.schemas.claves import GenerarIn, RevocarIn, RotarIn
from app.services.claves import ClavesServiceImpl
from app.services.errores import ErrorServicio


class ErrorGestor(RuntimeError):
    pass


class GestorFalso:
    ErrorGestorClaves = ErrorGestor

    def __init__(self):
        self.borrados = []

    def generar_par(self, tipo):
        return SimpleNamespace(algoritmo=f"alg-{tipo}", publica=b"pub", huella=f"huella-{tipo}", tamano_publica=3,
                               tamano_privada=4, privada_pem=bytearray(b"PRIVADA"))

    def borrar(self, buffer):
        self.borrados.append(buffer)

    def publica_pem(self, tipo, publica):
        return f"PEM-{tipo}"

    def descifrar_email(self, blob):
        return "persona@example.org"

    def email_hash_candidatos(self, texto):
        return ["h1"]


class RepoFalso:
    def __init__(self, usuario=None):
        self.claves: dict[uuid.UUID, ClaveUsuario] = {}
        self._usuario = usuario
        self.eventos: list[str] = []
        self.marcadas: list = []

    def obtener(self, clave_id, bloquear=False):
        self.eventos.append(f"obtener:{bloquear}")
        return self.claves.get(clave_id)

    def usuario(self, usuario_id):
        return self._usuario

    def existe_activa(self, usuario_id, tipo):
        return any(k.usuario_id == usuario_id and k.tipo == tipo and k.estado == EstadoClave.ACTIVA for k in self.claves.values())

    def listar(self, *args):
        return 0, []

    def agregar(self, clave):
        clave.creada_utc = utcnow()      # en la base lo pone el valor por defecto al insertar
        self.claves[clave.id] = clave

    def flush(self):
        self.eventos.append("flush")

    def commit(self):
        self.eventos.append("commit")

    def rollback(self):
        self.eventos.append("rollback")

    def marcar_entregadas(self, ids):
        self.marcadas.extend(ids)


class AuditorFalso:
    def __init__(self, falla=False):
        self.operaciones, self.falla = [], falla

    def registrar(self, operacion, clave, motivo=None, extra=None):
        if self.falla:
            raise ErrorServicio(c.HTTP_UNAVAILABLE, c.MSG_KEYS_AUDIT_FAILED)
        self.operaciones.append(operacion)


def _usuario(activo=True):
    return SimpleNamespace(id=uuid.uuid4(), activo=activo, nombre="Persona", email_cifrado=b"x")


def _servicio(usuario=None, auditor=None):
    repo, gestor = RepoFalso(usuario), GestorFalso()
    return ClavesServiceImpl(repo, auditor or AuditorFalso(), gestor), repo, gestor


def test_generar_entrega_las_privadas_una_vez_y_audita_cada_clave():
    usuario = _usuario()
    servicio, repo, _ = _servicio(usuario)
    pendiente = servicio.generar(GenerarIn(usuario_id=usuario.id))
    assert [k.tipo for k in pendiente.entrega.claves] == [TipoClave.KEM, TipoClave.FIRMA]
    assert all(k.privada_pem == "PRIVADA" for k in pendiente.entrega.claves)
    assert repo.eventos[-1] == "commit" and len(pendiente.ids) == 2
    assert servicio.auditor.operaciones == [c.OP_KEY_GENERATED] * 2


def test_generar_con_usuario_inexistente_o_inactivo():
    servicio, _, _ = _servicio(None)
    with pytest.raises(ErrorServicio) as e:
        servicio.generar(GenerarIn(usuario_id=uuid.uuid4()))
    assert e.value.status == c.HTTP_NOT_FOUND
    servicio, _, _ = _servicio(_usuario(activo=False))
    with pytest.raises(ErrorServicio) as e:
        servicio.generar(GenerarIn(usuario_id=uuid.uuid4()))
    assert e.value.status == c.HTTP_CONFLICT


def test_no_se_genera_si_ya_hay_una_activa_del_mismo_tipo():
    usuario = _usuario()
    servicio, _, _ = _servicio(usuario)
    servicio.generar(GenerarIn(usuario_id=usuario.id, tipos=[TipoClave.KEM]))
    with pytest.raises(ErrorServicio) as e:
        servicio.generar(GenerarIn(usuario_id=usuario.id, tipos=[TipoClave.KEM]))
    assert (e.value.status, e.value.detail) == (c.HTTP_CONFLICT, c.MSG_KEYS_ALREADY_ACTIVE.format(tipo="kem"))


def test_si_la_auditoria_falla_se_borran_las_privadas_y_no_se_confirma():
    usuario = _usuario()
    servicio, repo, gestor = _servicio(usuario, AuditorFalso(falla=True))
    with pytest.raises(ErrorServicio) as e:
        servicio.generar(GenerarIn(usuario_id=usuario.id))
    assert e.value.status == c.HTTP_UNAVAILABLE and "commit" not in repo.eventos and len(gestor.borrados) == 2


def test_revocar_pide_bloqueo_y_no_se_puede_revocar_dos_veces():
    usuario = _usuario()
    servicio, repo, _ = _servicio(usuario)
    clave = servicio.generar(GenerarIn(usuario_id=usuario.id, tipos=[TipoClave.KEM])).ids[0]
    salida = servicio.revocar(clave, RevocarIn(motivo="se perdió la clave"))
    assert salida.estado == EstadoClave.REVOCADA and "obtener:True" in repo.eventos
    with pytest.raises(ErrorServicio) as e:
        servicio.revocar(clave, RevocarIn(motivo="otra vez"))
    assert e.value.detail == c.MSG_KEYS_ALREADY_REVOKED


def test_rotar_revoca_la_vieja_y_enlaza_la_nueva():
    usuario = _usuario()
    servicio, repo, _ = _servicio(usuario)
    vieja = servicio.generar(GenerarIn(usuario_id=usuario.id, tipos=[TipoClave.FIRMA])).ids[0]
    pendiente = servicio.rotar(vieja, RotarIn())
    assert repo.claves[vieja].estado == EstadoClave.REVOCADA and repo.claves[vieja].reemplazada_por == pendiente.ids[0]
    assert servicio.auditor.operaciones[-1] == c.OP_KEY_ROTATED


def test_clave_inexistente_es_404_y_el_cierre_borra_las_privadas_aunque_falle_el_contador():
    usuario = _usuario()
    servicio, repo, gestor = _servicio(usuario)
    with pytest.raises(ErrorServicio) as e:
        servicio.huella(uuid.uuid4())
    assert e.value.status == c.HTTP_NOT_FOUND
    pendiente = servicio.generar(GenerarIn(usuario_id=usuario.id))
    repo.marcar_entregadas = lambda ids: (_ for _ in ()).throw(RuntimeError("base caída"))
    servicio.cerrar_entrega(pendiente)
    assert len(gestor.borrados) == 2


def test_el_gancho_antes_de_confirmar_ve_las_privadas_antes_del_commit():
    usuario = _usuario()
    servicio, repo, _ = _servicio(usuario)
    vistas = []
    servicio.generar(GenerarIn(usuario_id=usuario.id),
                     antes_de_confirmar=lambda ent: vistas.append((len(ent.entrega.claves), "commit" in repo.eventos)))
    assert vistas == [(2, False)] and repo.eventos[-1] == "commit"


def test_si_el_gancho_falla_no_se_confirma_y_se_borran_las_privadas():
    usuario = _usuario()
    servicio, repo, gestor = _servicio(usuario)

    def roto(_):
        raise OSError("disco lleno")

    with pytest.raises(OSError):
        servicio.generar(GenerarIn(usuario_id=usuario.id), antes_de_confirmar=roto)
    assert "commit" not in repo.eventos and len(gestor.borrados) == 2

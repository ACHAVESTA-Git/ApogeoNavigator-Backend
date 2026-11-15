"""Servicio de claves: reglas de negocio del gestor («revocar y reemitir»). Sin FastAPI ni SQL: usa un repositorio."""
import base64
import logging
import uuid
from contextlib import contextmanager
from sqlalchemy.exc import IntegrityError

from app.core import config as c
from app.db.models import Usuario, utcnow
from app.mappers import claves as mapper
from app.models.claves import ClaveUsuario, EstadoClave, TipoClave
from app.repositories.claves import ClavesRepository
from app.schemas.claves import ClaveOut, Entrega, GenerarIn, HuellaOut, PaginaClaves, PublicaOut, RevocarIn, RotarIn
from app.security import gestor_claves as gk
from app.services.claves.servicio import Auditor, EntregaPendiente
from app.services.errores import ErrorServicio

log = logging.getLogger(c.KEYS_LOGGER)

Creadas = list[tuple[ClaveUsuario, gk.ParNuevo]]


class ClavesServiceImpl:
    """`gestor` es el módulo de criptografía (inyectable para probar sin generar claves reales)."""

    def __init__(self, repo: ClavesRepository, auditor: Auditor, gestor=gk):
        self.repo = repo
        self.auditor = auditor
        self.gestor = gestor

    # ------------------------------------------------------------ lecturas
    def listar(self, usuario_id, estado, tipo, q, limite, offset) -> PaginaClaves:
        busqueda = None
        if q:
            try:
                hashes = self.gestor.email_hash_candidatos(q)
            except self.gestor.ErrorGestorClaves:      # sin claves del servidor (producción mal configurada): solo por nombre
                hashes = []
            busqueda = (q, hashes)
        total, filas = self.repo.listar(usuario_id, estado, tipo, busqueda, limite, offset)
        self._log_lectura(c.OP_KEY_READ + ":listar",
                          f"usuario_id={bool(usuario_id)} estado={estado} tipo={tipo} q={bool(q)} limite={limite} offset={offset}",
                          len(filas))
        return PaginaClaves(total=total, limite=limite, offset=offset, items=[self._out(k, u) for k, u in filas])

    def publica(self, clave_id: uuid.UUID, formato: str) -> PublicaOut:
        """Pública (también si está revocada: sirve para verificar lo firmado antes)."""
        clave = self._get(clave_id)
        pem = self.gestor.publica_pem(clave.tipo.value, clave.clave_publica)
        self._log_lectura(c.OP_KEY_READ + ":publica", f"clave_id={clave.id} formato={formato}", 1)
        return PublicaOut(id=clave.id, tipo=clave.tipo, algoritmo=clave.algoritmo, estado=clave.estado,
                          huella_sha3_256=clave.huella_sha3_256, clave_publica_pem=pem,
                          clave_publica_b64=base64.b64encode(clave.clave_publica).decode("ascii"))

    def huella(self, clave_id: uuid.UUID) -> HuellaOut:
        clave = self._get(clave_id)
        self._log_lectura(c.OP_KEY_READ + ":huella", f"clave_id={clave.id}", 1)
        return HuellaOut(id=clave.id, algoritmo=clave.algoritmo, estado=clave.estado, huella_sha3_256=clave.huella_sha3_256)

    # ------------------------------------------------------------ cambios
    def generar(self, cuerpo: GenerarIn) -> EntregaPendiente:
        usuario = self._usuario_activo(cuerpo.usuario_id)
        tipos = list(dict.fromkeys(cuerpo.tipos))
        for tipo in tipos:
            if self.repo.existe_activa(usuario.id, tipo):
                raise ErrorServicio(c.HTTP_CONFLICT, c.MSG_KEYS_ALREADY_ACTIVE.format(tipo=tipo.value))
        creadas = [self._nueva_clave(usuario, tipo.value) for tipo in tipos]
        with self._cambio(creadas):
            self.repo.flush()
            for clave, _ in creadas:
                self.auditor.registrar(c.OP_KEY_GENERATED, clave)
        return self._entregar(usuario, creadas)

    def revocar(self, clave_id: uuid.UUID, cuerpo: RevocarIn) -> ClaveOut:
        clave = self._get(clave_id, bloquear=True)
        if clave.estado == EstadoClave.REVOCADA:
            raise ErrorServicio(c.HTTP_CONFLICT, c.MSG_KEYS_ALREADY_REVOKED)
        clave.estado, clave.revocada_utc, clave.motivo_revocacion = EstadoClave.REVOCADA, utcnow(), cuerpo.motivo
        with self._cambio([]):
            self.repo.flush()
            self.auditor.registrar(c.OP_KEY_REVOKED, clave, motivo=cuerpo.motivo)
        usuario = self.repo.usuario(clave.usuario_id)
        self.repo.commit()
        return self._out(clave, usuario)

    def rotar(self, clave_id: uuid.UUID, cuerpo: RotarIn | None) -> EntregaPendiente:
        """Revoca la clave y emite otra del mismo tipo para el mismo usuario (la privada nueva se entrega una vez)."""
        motivo = (cuerpo or RotarIn()).motivo
        vieja = self._get(clave_id, bloquear=True)
        if vieja.estado == EstadoClave.REVOCADA:
            raise ErrorServicio(c.HTTP_CONFLICT, c.MSG_KEYS_ALREADY_REVOKED)
        usuario = self._usuario_activo(vieja.usuario_id)
        vieja.estado, vieja.revocada_utc, vieja.motivo_revocacion = EstadoClave.REVOCADA, utcnow(), motivo
        creadas: Creadas = []
        with self._cambio(creadas):
            self.repo.flush()                             # libera el hueco del índice de «una activa por tipo»
            creadas.append(self._nueva_clave(usuario, vieja.tipo.value))
            nueva = creadas[0][0]
            self.repo.flush()
            vieja.reemplazada_por = nueva.id
            self.repo.flush()
            self.auditor.registrar(c.OP_KEY_ROTATED, nueva, motivo=motivo,
                                   extra={"reemplaza_a": str(vieja.id), "huella_anterior": vieja.huella_sha3_256})
        return self._entregar(usuario, creadas)

    def cerrar_entrega(self, pendiente: EntregaPendiente) -> None:
        """Se ejecuta DESPUÉS de enviar la respuesta: cuenta la entrega (backups=1) y borra la privada de memoria."""
        try:
            self.repo.marcar_entregadas(pendiente.ids)
        except Exception as exc:  # noqa: BLE001  (la entrega ya ocurrió; solo se registra el fallo del contador)
            log.error(c.MSG_KEYS_DELIVERY_COUNT_FAILED, type(exc).__name__)
        finally:
            for buffer in pendiente.buffers:
                self.gestor.borrar(buffer)

    # ------------------------------------------------------------ internos
    def _email(self, usuario: Usuario | None) -> str | None:
        """Email descifrado del usuario (se guarda cifrado). None si no hay usuario o su clave ya no está configurada."""
        if usuario is None:
            return None
        try:
            return self.gestor.descifrar_email(usuario.email_cifrado)
        except self.gestor.ErrorGestorClaves:
            return None

    def _out(self, clave: ClaveUsuario, usuario: Usuario | None = None) -> ClaveOut:
        return mapper.clave_out(clave, usuario, self._email(usuario))

    @staticmethod
    def _log_lectura(operacion: str, filtros: str, resultados: int) -> None:
        """Las lecturas no entran en la cadena firmada: dejan rastro en el log (sin emails ni material de claves)."""
        log.info(c.MSG_KEYS_READ_LOG, operacion, filtros, resultados)

    def _get(self, clave_id: uuid.UUID, bloquear: bool = False) -> ClaveUsuario:
        clave = self.repo.obtener(clave_id, bloquear)
        if clave is None:
            raise ErrorServicio(c.HTTP_NOT_FOUND, c.MSG_KEYS_NO_KEY)
        return clave

    def _usuario_activo(self, usuario_id: uuid.UUID) -> Usuario:
        usuario = self.repo.usuario(usuario_id)
        if usuario is None:
            raise ErrorServicio(c.HTTP_NOT_FOUND, c.MSG_KEYS_NO_USER)
        if not usuario.activo:
            raise ErrorServicio(c.HTTP_CONFLICT, c.MSG_KEYS_INACTIVE_USER)
        return usuario

    def _nueva_clave(self, usuario: Usuario, tipo: str) -> tuple[ClaveUsuario, gk.ParNuevo]:
        par = self.gestor.generar_par(tipo)
        clave = ClaveUsuario(id=uuid.uuid4(), usuario_id=usuario.id, tipo=TipoClave(tipo), algoritmo=par.algoritmo,
                             clave_publica=par.publica, huella_sha3_256=par.huella, tamano_publica=par.tamano_publica,
                             tamano_privada=par.tamano_privada, estado=EstadoClave.ACTIVA, backups=0)
        self.repo.agregar(clave)
        return clave, par

    @contextmanager
    def _cambio(self, creadas: Creadas):
        """Protege una escritura: ante cualquier fallo borra las privadas generadas; un choque con el índice único
        «una activa por usuario y tipo» (dos peticiones simultáneas) es un 409, no un 500."""
        try:
            yield
        except IntegrityError:
            self.repo.rollback()
            self._borrar_todas(creadas)
            raise ErrorServicio(c.HTTP_CONFLICT, c.MSG_KEYS_ALREADY_ACTIVE_GENERIC) from None
        except BaseException:
            self._borrar_todas(creadas)
            raise

    def _borrar_todas(self, creadas: Creadas) -> None:
        for _, par in creadas:
            self.gestor.borrar(par.privada_pem)

    def _entregar(self, usuario: Usuario, creadas: Creadas) -> EntregaPendiente:
        """Confirma la transacción y prepara las privadas para devolverlas UNA vez."""
        with self._cambio(creadas):
            self.repo.commit()
        claves = [mapper.clave_entregada(self._out(clave, usuario), self.gestor.publica_pem(clave.tipo.value, clave.clave_publica),
                                         par.privada_pem.decode("ascii")) for clave, par in creadas]
        return EntregaPendiente(entrega=Entrega(aviso=c.MSG_KEYS_WARNING, claves=claves),
                                ids=[clave.id for clave, _ in creadas], buffers=[par.privada_pem for _, par in creadas])

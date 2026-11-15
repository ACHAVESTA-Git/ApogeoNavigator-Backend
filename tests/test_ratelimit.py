"""Limitador de ventana deslizante: ventana, memoria acotada y claves que no se crean al mirar."""
from app.utils.ratelimit import SlidingWindowLimiter


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_limita_y_libera_con_la_ventana():
    reloj = Reloj()
    lim = SlidingWindowLimiter(2, 10.0, reloj)
    assert lim.hit("a") == 0 and lim.hit("a") == 0
    assert lim.hit("a") == 10.0
    reloj.t = 10.0
    assert lim.hit("a") == 0


def test_peek_no_crea_claves_ni_registra():
    lim = SlidingWindowLimiter(1, 10.0, Reloj())
    for i in range(5000):
        assert lim.peek(f"ip-{i}") == 0
    assert len(lim) == 0
    assert lim.hit("x") == 0 and lim.peek("x") > 0 and len(lim) == 1


def test_claves_con_historial_vacio_se_borran():
    reloj = Reloj()
    lim = SlidingWindowLimiter(1, 10.0, reloj)
    for i in range(100):
        lim.hit(f"ip-{i}")
    assert len(lim) == 100
    reloj.t = 11.0
    lim.peek("cualquiera")
    assert len(lim) == 100            # peek solo purga la clave consultada
    for i in range(100):
        lim.peek(f"ip-{i}")
    assert len(lim) == 0


def test_tope_de_claves_expulsa_las_mas_antiguas():
    lim = SlidingWindowLimiter(1, 60.0, Reloj(), max_keys=50)
    for i in range(5000):                                   # 5000 «clientes» falsos
        lim.hit(f"falsa-{i}")
    assert len(lim) == 50
    assert lim.peek("falsa-0") == 0 and lim.peek("falsa-4999") > 0       # la antigua se expulsó; la reciente sigue


def test_una_clave_bloqueada_que_insiste_no_se_expulsa_primero():
    lim = SlidingWindowLimiter(1, 60.0, Reloj(), max_keys=3)
    lim.hit("a"); lim.hit("b"); lim.hit("c")
    assert lim.hit("a") > 0                                  # 'a' insiste: pasa a ser la más reciente
    lim.hit("d")
    assert lim.peek("a") > 0 and lim.peek("b") == 0

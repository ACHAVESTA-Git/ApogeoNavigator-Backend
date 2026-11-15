"""Pruebas rápidas de los utilitarios de la validación de la proyección 2028 (sin red)."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts" / "datos"))
import validar_proyeccion_2028 as vp  # noqa: E402


def test_dif_circular_envuelve():
    assert vp.dif_circular(359.0, 1.0) == pytest.approx(-2.0)
    assert vp.dif_circular(1.0, 359.0) == pytest.approx(2.0)


def test_arco_entre_direcciones():
    assert vp.arco_deg(0, 0, 90, 0) == pytest.approx(90.0)
    assert vp.arco_deg(10, 20, 10, 20) == pytest.approx(0.0, abs=1e-6)
    assert vp.arco_deg(0, 89, 180, 89) == pytest.approx(2.0)


def test_cruces_ascendentes_interpola():
    t = np.arange(4.0)
    assert vp.cruces_ascendentes(t, np.array([-1.0, -0.5, 1.5, 2.0])) == pytest.approx([1.25])


def test_modelo_json_tiene_bloque_error_completo():
    err = json.loads(vp.MODELO_JSON.read_text(encoding="utf-8"))["error"]
    assert [h["meses"] for h in err["por_horizonte"]] == list(range(1, 15))
    assert len(err["control_horizons"]) == 36
    assert err["aciertos_semaforo_control"]["total"] == 126

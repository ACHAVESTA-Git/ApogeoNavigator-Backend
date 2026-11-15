"""Espectro, ajuste armónico y proyección con señales sintéticas de solución conocida."""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.analysis import frames, harmonic, projection, spectrum
from app.core import config as c

START = datetime(2026, 12, 1, tzinfo=timezone.utc)


def test_fft_encuentra_el_periodo_y_los_candidatos_conocidos():
    t = np.arange(24 * 180) / 24
    peak = spectrum.dominant_periods(np.sin(2 * np.pi * t / 29.53) + 0.2 * np.sin(2 * np.pi * t / 14.77), 1 / 24)[0]
    assert peak.period_days == pytest.approx(29.53, abs=0.2) and peak.amplitude == 1.0
    assert "sinodico" in peak.candidates


def test_fft_con_banda_ignora_los_picos_de_fuera():
    t = np.arange(24 * 180) / 24
    signal = 3 * np.sin(2 * np.pi * t / 90) + np.sin(2 * np.pi * t / 29.53)
    assert spectrum.dominant_periods(signal, 1 / 24, band=c.FFT_SYNODIC_BAND_DAYS)[0].period_days == pytest.approx(29.5, abs=0.4)


def test_resolucion_de_la_fft_no_separa_anomalistico_y_draconico():
    names = spectrum.known_candidates(27.4, window_days=182)
    assert "anomalistico" in names and "draconico" in names and "sinodico" not in names


def test_ajuste_recupera_amplitudes_y_extrapola():
    t = np.arange(24 * 180) / 24
    freqs = [1 / 29.53, 1 / 14.0]
    truth = lambda x: 4 + 3 * np.sin(2 * np.pi * x / 29.53 + 0.7) - 1.5 * np.cos(2 * np.pi * x / 14.0)   # noqa: E731
    model = harmonic.fit(t, truth(t), freqs)
    future = np.linspace(200, 540, 50)
    assert np.max(np.abs(model.predict(future) - truth(future))) < 1e-5


def test_ajuste_con_varias_columnas_y_frecuencias_repetidas_o_negativas():
    t = np.arange(24 * 100) / 24
    y = np.column_stack([np.sin(2 * np.pi * t / 20), np.cos(2 * np.pi * t / 20)])
    model = harmonic.fit(t, y, [1 / 20, -1 / 20, 1 / 20])
    assert model.frequencies == (0.05,) and model.predict(t).shape == (len(t), 2)
    assert np.max(np.abs(model.predict(t) - y)) < 1e-6


def test_afinado_del_periodo():
    t = np.arange(24 * 180) / 24
    y = 5 * np.sin(2 * np.pi * t / 29.2) + 2 * np.cos(2 * np.pi * 2 * t / 29.2)
    found = harmonic.refine_period(t, y, lambda f: [f, 2 * f], center=29.5)
    assert found == pytest.approx(29.2, abs=0.01)


@pytest.mark.parametrize("az,el", [(0.0, 0.0), (359.9, 10.0), (123.4, -45.0), (200.0, 89.9), (10.0, -89.9)])
def test_vector_de_direccion_ida_y_vuelta(az, el):
    back_az, back_el = harmonic.angles_of(harmonic.direction_vector(np.array([az]), np.array([el])))
    assert back_el[0] == pytest.approx(el, abs=1e-9)
    if abs(el) < 89:
        assert harmonic.circular_error(back_az, np.array([az]))[0] == pytest.approx(0, abs=1e-7)


def test_error_circular_cruza_el_norte():
    assert harmonic.circular_error(np.array([359.0]), np.array([1.0]))[0] == pytest.approx(-2.0)
    assert harmonic.circular_error(np.array([1.0]), np.array([359.0]))[0] == pytest.approx(2.0)


def synthetic_frame(days: float) -> "frames.pl.LazyFrame":
    """Sol que gira cada mes sinódico, con una oscilación anomalística; Tierra casi fija con libración."""
    rows = []
    for i in range(int(days * 24)):
        d = i / 24
        hour_angle = 2 * np.pi * d / 29.53 + 0.1 * np.sin(2 * np.pi * d / c.ANOMALISTIC_MONTH_DAYS)
        sun_vec = np.array([-np.sin(hour_angle) * 0.9, 0.1, np.cos(hour_angle) * 0.9])
        sun_vec = sun_vec / np.linalg.norm(sun_vec)
        az, el = harmonic.angles_of(sun_vec[None, :])
        rows.append({c.COL_UTC: START + timedelta(hours=i), c.COL_SOL_AZ: float(az[0]), c.COL_SOL_EL: float(el[0]),
                     c.COL_TIERRA_AZ: 200 + 7 * math.sin(2 * math.pi * d / c.ANOMALISTIC_MONTH_DAYS),
                     c.COL_TIERRA_EL: 30 + 5 * math.sin(2 * math.pi * d / c.DRACONIC_MONTH_DAYS)})
    return frames.from_rows(rows)


def test_proyeccion_sintetica_extrapola_con_poco_error():
    model = projection.fit_projection(synthetic_frame(182))
    assert model.synodic_days == pytest.approx(29.53, abs=0.1)
    truth = synthetic_frame(182 + 120).collect().tail(24 * 100)
    pred = model.predict(truth[c.COL_UTC].to_list())
    for column in (c.COL_SOL_EL, c.COL_TIERRA_EL):
        assert np.sqrt(np.mean((pred[column].to_numpy() - truth[column].to_numpy()) ** 2)) < 1.5
    for column in (c.COL_TIERRA_AZ,):
        err = harmonic.circular_error(pred[column].to_numpy(), truth[column].to_numpy())
        assert np.sqrt(np.mean(err ** 2)) < 1.5


def test_ventana_corta_se_rechaza():
    with pytest.raises(ValueError, match="al menos"):
        projection.fit_projection(synthetic_frame(40))


def test_la_proyeccion_devuelve_angulos_validos():
    model = projection.fit_projection(synthetic_frame(100))
    pred = model.predict([START + timedelta(days=d) for d in (50, 150, 400)])
    assert pred[c.COL_SOL_AZ].is_between(0, 360, closed="left").all() and pred[c.COL_TIERRA_AZ].is_between(0, 360, closed="left").all()
    assert pred[c.COL_SOL_EL].is_between(-90, 90).all()


def test_referencia_ingenua_repite_el_ultimo_ciclo():
    frame = synthetic_frame(100)
    times = [START + timedelta(days=100 + 29.53 * 2)]
    base = projection.persistence_baseline(frame, times)
    assert set(base) == {c.COL_SOL_AZ, c.COL_SOL_EL, c.COL_TIERRA_AZ, c.COL_TIERRA_EL}

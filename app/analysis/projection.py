"""Proyección armónica del cielo lunar: Sol y Tierra (azimut y elevación) más allá de los datos.

Se ajusta el vector unitario de dirección (este, norte, arriba) de cada astro, no los ángulos: el vector es suave
incluso cuando el azimut gira de golpe cerca del cénit, y no hace falta desenrollar ángulos. Después se normaliza y
se convierte a azimut y elevación.

Diseño (elegido con validación cruzada dentro de los datos y con lo identificable en 6 meses):
  * Sol: armónicos k·f_sinódico, f_anomalístico y sus armónicos, y las bandas laterales k·f_sinódico ± f_anomalístico,
    que incluyen el batido de ~411 días. El periodo sinódico se afina alrededor del pico de la FFT.
  * Tierra: libración con periodos físicos fijos: f_anomalístico (longitud), f_dracónico (latitud) y los términos
    solares de la «variación» (2·f_sinódico) y la «evección» (2·f_sinódico ± f_anomalístico). Con 6 meses la
    FFT no separa el anomalístico del dracónico y afinarlos empeora la proyección.
  * Elevación del Sol: se elige entre la del vector y un ajuste directo de la elevación con una validación cruzada
    (entrena con los dos primeros tercios y prueba en el último). El directo gana con el Sol rasante de los polos.
  * Sin periodos largos independientes (anual, año de eclipses): con menos de un ciclo no son identificables
    y la extrapolación se dispara.
No sustituye a las efemérides físicas: es una estimación para explorar tendencias y animar el cielo.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import polars as pl

from app.analysis import harmonic, spectrum
from app.analysis.frames import T_DAYS, VALUE_COLUMNS
from app.core import config as c

SECONDS_PER_DAY = c.SECONDS_PER_HOUR * c.HOURS_PER_DAY
FA = 1.0 / c.ANOMALISTIC_MONTH_DAYS
FD = 1.0 / c.DRACONIC_MONTH_DAYS


def sun_frequencies(f_synodic: float) -> list[float]:
    freqs = [k * f_synodic for k in range(1, c.SUN_SYNODIC_HARMONICS + 1)]
    freqs += [m * FA for m in range(1, c.SUN_ANOMALISTIC_HARMONICS + 1)]
    freqs += [k * f_synodic + sign * FA for k in range(1, c.SUN_SIDEBAND_HARMONICS + 1) for sign in (1, -1)]
    return freqs


def earth_frequencies() -> list[float]:
    """Libración: anomalístico (longitud), dracónico (latitud), la «variación» 2·f_sinódico y la «evección»
    2·f_sinódico ± f_anomalístico, con los periodos físicos conocidos. Elegido por validación cruzada dentro
    de los datos de entrenamiento, no con la verdad de terreno."""
    f_syn = 1.0 / c.SYNODIC_MONTH_DAYS
    return [FA, FD, 2 * FA, 2 * f_syn, 2 * f_syn + FA, abs(2 * f_syn - FA)]


SUN, EARTH = c.KEY_SOL, c.KEY_TIERRA
COLUMNS = {SUN: (c.COL_SOL_AZ, c.COL_SOL_EL), EARTH: (c.COL_TIERRA_AZ, c.COL_TIERRA_EL)}


@dataclass(frozen=True)
class SiteProjection:
    t0: datetime                          # instante de la primera fila (t = 0)
    last_t_days: float
    window_days: float
    synodic_days: float                   # periodo sinódico afinado con los datos
    peaks: tuple[spectrum.Peak, ...]      # picos de la FFT del Sol hacia el este (informativo)
    models: dict[str, harmonic.HarmonicModel]
    sun_elevation_direct: harmonic.HarmonicModel | None = None   # sustituye a la elevación del vector si ganó la CV

    def days_of(self, times: Sequence[datetime]) -> np.ndarray:
        return np.array([(t - self.t0).total_seconds() / SECONDS_PER_DAY for t in times])

    def predict_days(self, t_days: np.ndarray) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for body, (az_col, el_col) in COLUMNS.items():
            out[az_col], out[el_col] = harmonic.angles_of(self.models[body].predict(t_days))
        if self.sun_elevation_direct is not None:
            out[c.COL_SOL_EL] = self.sun_elevation_direct.predict(t_days)
        return out

    def predict(self, times: Sequence[datetime]) -> pl.DataFrame:
        values = self.predict_days(self.days_of(times))
        return pl.DataFrame({c.COL_UTC: list(times), **values}, schema_overrides={c.COL_UTC: pl.Datetime("us", "UTC")})


def _arrays(frame: pl.LazyFrame) -> tuple[datetime, np.ndarray, dict[str, np.ndarray]]:
    data = frame.select(c.COL_UTC, T_DAYS, *VALUE_COLUMNS).collect()
    return (data[c.COL_UTC][0], data[T_DAYS].to_numpy(),
            {col: data[col].to_numpy().astype(float) for col in VALUE_COLUMNS})


def fit_projection(frame: pl.LazyFrame) -> SiteProjection:
    """Ajusta los modelos del Sol y de la Tierra con todas las filas de `frame` (ordenadas, paso uniforme)."""
    t0, t, y = _arrays(frame)
    window = float(t[-1] - t[0])
    if window < c.PROJECTION_MIN_WINDOW_DAYS:
        raise ValueError(c.MSG_SIM_NOT_ENOUGH_DATA.format(days=c.PROJECTION_MIN_WINDOW_DAYS))
    vectors = {body: harmonic.direction_vector(y[az], y[el]) for body, (az, el) in COLUMNS.items()}
    peaks = spectrum.dominant_periods(vectors[SUN][:, 0], float(t[1] - t[0]), band=c.FFT_SYNODIC_BAND_DAYS)
    synodic = harmonic.refine_period(t, vectors[SUN], sun_frequencies,
                                     peaks[0].period_days if peaks else c.SYNODIC_MONTH_DAYS)
    sun_freqs = sun_frequencies(1.0 / synodic)
    models = {SUN: harmonic.fit(t, vectors[SUN], sun_freqs), EARTH: harmonic.fit(t, vectors[EARTH], earth_frequencies())}
    direct = _direct_elevation(t, y[c.COL_SOL_EL], vectors[SUN], sun_freqs, synodic)
    return SiteProjection(t0, float(t[-1]), window, synodic, tuple(peaks), models, direct)


def _fit_direct(t: np.ndarray, sun_el: np.ndarray, center: float) -> harmonic.HarmonicModel:
    """Ajuste directo de la elevación con su propio periodo sinódico afinado."""
    period = harmonic.refine_period(t, sun_el, sun_frequencies, center)
    return harmonic.fit(t, sun_el, sun_frequencies(1.0 / period))


def _direct_elevation(t: np.ndarray, sun_el: np.ndarray, sun_vector: np.ndarray, freqs: Sequence[float],
                      center: float) -> harmonic.HarmonicModel | None:
    """Validación cruzada: ¿predice mejor el último tercio la elevación ajustada directamente que la del vector?
    Devuelve el modelo directo (entrenado con todos los datos) solo si gana."""
    cut = int(len(t) * (1 - c.CV_HOLDOUT_FRACTION))
    by_vector = harmonic.angles_of(harmonic.fit(t[:cut], sun_vector[:cut], freqs).predict(t[cut:]))[1]
    direct = _fit_direct(t[:cut], sun_el[:cut], center).predict(t[cut:])
    rmse = lambda pred: float(np.sqrt(np.mean((pred - sun_el[cut:]) ** 2)))   # noqa: E731
    return _fit_direct(t, sun_el, center) if rmse(direct) < rmse(by_vector) else None


def persistence_baseline(frame: pl.LazyFrame, times: Sequence[datetime]) -> dict[str, np.ndarray]:
    """Referencia ingenua: repetir el último ciclo (sinódico para el Sol, anomalístico para la Tierra)."""
    t0, t, y = _arrays(frame)
    out: dict[str, np.ndarray] = {}
    target = np.array([(x - t0).total_seconds() / SECONDS_PER_DAY for x in times])
    for body, period in ((SUN, c.SYNODIC_MONTH_DAYS), (EARTH, c.ANOMALISTIC_MONTH_DAYS)):
        cycles = np.ceil((target - t[-1]) / period)
        source = np.clip(np.round((target - cycles * period - t[0]) / (t[1] - t[0])), 0, len(t) - 1).astype(int)
        for column in COLUMNS[body]:
            out[column] = y[column][source]
    return out

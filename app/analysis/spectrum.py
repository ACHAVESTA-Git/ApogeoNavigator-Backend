"""Periodograma (FFT con ventana de Hann y relleno de ceros) y picos dominantes de una serie uniforme."""
from dataclasses import dataclass

import numpy as np

from app.core import config as c


@dataclass(frozen=True)
class Peak:
    period_days: float
    amplitude: float                    # relativa al pico más alto de la serie (0..1)
    candidates: tuple[str, ...]         # periodos conocidos dentro de la resolución de la FFT


def periodogram(values: np.ndarray, step_days: float) -> tuple[np.ndarray, np.ndarray]:
    """(frecuencias en ciclos/día, amplitud). Se quita la tendencia lineal antes de la FFT."""
    n = len(values)
    t = np.arange(n)
    detrended = values - np.polyval(np.polyfit(t, values, 1), t)
    padded = 1 << (n * c.FFT_PAD_FACTOR - 1).bit_length()
    amplitude = np.abs(np.fft.rfft(detrended * np.hanning(n), padded))
    return np.fft.rfftfreq(padded, d=step_days), amplitude


def _interpolate(freqs: np.ndarray, amp: np.ndarray, i: int) -> float:
    """Frecuencia del pico refinada con una parábola por los tres puntos vecinos."""
    a, b, d = amp[i - 1], amp[i], amp[i + 1]
    denom = a - 2 * b + d
    shift = 0.5 * (a - d) / denom if denom else 0.0
    return float(freqs[i] + shift * (freqs[1] - freqs[0]))


def known_candidates(period_days: float, window_days: float) -> tuple[str, ...]:
    """Periodos conocidos a menos de la mitad de la resolución de Rayleigh (P²/T) del pico."""
    tolerance = c.FFT_MATCH_RESOLUTION_FRACTION * period_days ** 2 / window_days
    return tuple(name for name, known in c.KNOWN_PERIODS_DAYS.items() if abs(known - period_days) <= tolerance)


def dominant_periods(values: np.ndarray, step_days: float, top: int = c.FFT_TOP_PEAKS,
                     band: tuple[float, float] | None = None) -> list[Peak]:
    """Máximos locales del espectro, de mayor a menor amplitud. `band` limita los periodos (días)."""
    freqs, amp = periodogram(values, step_days)
    window_days = len(values) * step_days
    peaks: list[tuple[float, float]] = []
    for i in range(1, len(amp) - 1):
        if freqs[i] <= 0 or not (amp[i] > amp[i - 1] and amp[i] >= amp[i + 1]):
            continue
        period = 1.0 / _interpolate(freqs, amp, i)
        if period < c.FFT_MIN_PERIOD_DAYS or (band and not band[0] <= period <= band[1]):
            continue
        peaks.append((period, float(amp[i])))
    peaks.sort(key=lambda p: p[1], reverse=True)
    peak_max = peaks[0][1] if peaks else 1.0
    return [Peak(p, a / peak_max, known_candidates(p, window_days)) for p, a in peaks[:top]]

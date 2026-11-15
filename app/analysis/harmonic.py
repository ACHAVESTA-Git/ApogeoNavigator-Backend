"""Ajuste armónico por mínimos cuadrados regularizados (suma de senos y cosenos) de una o varias columnas."""
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from app.core import config as c

FULL_CIRCLE = 360.0


def circular_error(predicted: np.ndarray, actual: np.ndarray) -> np.ndarray:
    """Diferencia angular en (−180, 180]."""
    return (predicted - actual + FULL_CIRCLE / 2) % FULL_CIRCLE - FULL_CIRCLE / 2


def direction_vector(azimuth_deg: np.ndarray, elevation_deg: np.ndarray) -> np.ndarray:
    """Vector unitario (este, norte, arriba), n×3. Es suave incluso cuando el azimut gira de golpe en el cénit."""
    az, el = np.deg2rad(azimuth_deg), np.deg2rad(elevation_deg)
    return np.column_stack([np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)])


def angles_of(vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(azimut en [0, 360), elevación) de vectores n×3 que se normalizan antes."""
    unit = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    return (np.rad2deg(np.arctan2(unit[:, 0], unit[:, 1])) % FULL_CIRCLE,
            np.rad2deg(np.arcsin(np.clip(unit[:, 2], -1.0, 1.0))))


def design_matrix(t: np.ndarray, frequencies: Sequence[float]) -> np.ndarray:
    columns = [np.ones_like(t)]
    for freq in frequencies:
        phase = 2 * np.pi * freq * t
        columns += [np.cos(phase), np.sin(phase)]
    return np.column_stack(columns)


def _solve(design: np.ndarray, y: np.ndarray) -> np.ndarray:
    gram = design.T @ design
    ridge = c.HARMONIC_RIDGE * np.trace(gram) / gram.shape[0]
    return np.linalg.solve(gram + ridge * np.eye(gram.shape[0]), design.T @ y)


@dataclass(frozen=True)
class HarmonicModel:
    frequencies: tuple[float, ...]       # ciclos/día
    coefficients: np.ndarray             # (1 + 2·frecuencias) × columnas

    def predict(self, t_days: np.ndarray) -> np.ndarray:
        return design_matrix(np.asarray(t_days, dtype=float), self.frequencies) @ self.coefficients


def fit(t_days: np.ndarray, y: np.ndarray, frequencies: Sequence[float]) -> HarmonicModel:
    """Ajusta `y` (n o n×k) con las frecuencias dadas; las negativas se pliegan y las repetidas se unen."""
    freqs = tuple(sorted({round(abs(f), 9) for f in frequencies}))
    return HarmonicModel(freqs, _solve(design_matrix(t_days, freqs), y))


def refine_period(t_days: np.ndarray, y: np.ndarray, build: Callable[[float], Sequence[float]],
                  center: float) -> float:
    """Periodo (días) que minimiza el error cuadrático total del ajuste, con una malla alrededor de `center`
    que se va afinando. Usa una de cada `REFINE_STRIDE_SAMPLES` muestras por velocidad."""
    stride = c.REFINE_STRIDE_SAMPLES
    t, target = t_days[::stride], y[::stride]
    low, high = center - c.REFINE_HALF_BAND_DAYS, center + c.REFINE_HALF_BAND_DAYS
    best = center
    for _ in range(c.REFINE_ITERATIONS):
        periods = np.linspace(low, high, c.REFINE_GRID_POINTS)
        errors = []
        for period in periods:
            design = design_matrix(t, build(1.0 / period))
            errors.append(float(np.sum((design @ _solve(design, target) - target) ** 2)))
        best = float(periods[int(np.argmin(errors))])
        half = (high - low) / (c.REFINE_GRID_POINTS - 1)
        low, high = best - half, best + half
    return best

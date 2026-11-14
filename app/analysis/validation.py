"""Validación de la proyección contra la verdad de terreno (Horizons): error en grados, puestas y semáforo."""
import numpy as np
import polars as pl

from app.analysis import harmonic, pipeline, projection
from app.analysis.frames import SOL_VISIBLE, TIERRA_VISIBLE, VALUE_COLUMNS
from app.core import config as c
from app.services import lectura

ANGLE_COLUMNS = (c.COL_SOL_AZ, c.COL_TIERRA_AZ)
ROUND = c.ROUND_DEG
SECONDS_PER_DAY = c.SECONDS_PER_HOUR * c.HOURS_PER_DAY


def _error(column: str, predicted: np.ndarray, actual: np.ndarray) -> np.ndarray:
    return harmonic.circular_error(predicted, actual) if column in ANGLE_COLUMNS else predicted - actual


def _clean(value: float) -> float | None:
    return None if value is None or not np.isfinite(value) else round(float(value), ROUND)


def error_by_month(truth: pl.DataFrame, pred: pl.DataFrame, last_t_days: float, t0) -> dict[str, dict]:
    """MAE y RMSE totales de cada variable y RMSE por mes de horizonte (0 = primer mes tras los datos)."""
    days = np.array([(t - t0).total_seconds() / SECONDS_PER_DAY for t in truth[c.COL_UTC].to_list()])
    month = np.clip(np.floor((days - last_t_days) / c.MONTH_DAYS), 0, c.PROJECTION_MONTHS_AHEAD - 1).astype(int)
    out = {}
    for column in VALUE_COLUMNS:
        err = _error(column, pred[column].to_numpy(), truth[column].to_numpy())
        by_month = [_clean(np.sqrt(np.mean(err[month == m] ** 2))) if np.any(month == m) else None
                    for m in range(c.PROJECTION_MONTHS_AHEAD)]
        out[column] = {"mae_deg": _clean(np.mean(np.abs(err))), "rmse_deg": _clean(np.sqrt(np.mean(err ** 2))),
                       "rmse_por_mes_deg": by_month}
    return out


def _event_times(frame: pl.DataFrame, visible_column: str) -> dict[str, np.ndarray]:
    events = pipeline.transitions(frame.lazy(), visible_column).collect()
    return {kind: events.filter(pl.col(pipeline.EVENT_KIND) == kind)[c.COL_UTC].dt.epoch("s").to_numpy()
            for kind in (pipeline.KIND_SET, pipeline.KIND_RISE)}


def event_errors(truth: pl.DataFrame, pred: pl.DataFrame, elevation: str, visible: str) -> dict:
    """Error en horas de las puestas y salidas proyectadas respecto a las reales (emparejadas a < 24 h)."""
    real = _event_times(pipeline.with_visibility(truth.lazy()).collect(), visible)
    estimated = _event_times(pipeline.with_visibility(pred.lazy()).collect(), visible)
    diffs, total = [], 0
    for kind, times in real.items():
        total += len(times)
        for moment in times:
            if len(estimated[kind]):
                gap = np.min(np.abs(estimated[kind] - moment)) / c.SECONDS_PER_HOUR
                if gap <= c.EVENT_MATCH_MAX_HOURS:
                    diffs.append(gap)
    diffs = np.array(diffs)
    return {"eventos_reales": total, "eventos_proyectados": sum(len(v) for v in estimated.values()),
            "emparejados_pct": _clean(100 * len(diffs) / total) if total else None,
            "mae_h": _clean(diffs.mean()) if len(diffs) else None,
            "mediana_h": _clean(np.median(diffs)) if len(diffs) else None,
            "max_h": _clean(diffs.max()) if len(diffs) else None}


def semaforo_hits(truth: pl.DataFrame, pred: pl.DataFrame, step_h: float) -> dict:
    """Meses completos en que el semáforo proyectado coincide con el real (energía, comunicación y global)."""
    min_samples = 0.9 * c.MONTH_DAYS * c.HOURS_PER_DAY / step_h
    months = {}
    for name, frame in (("real", truth), ("proyeccion", pred)):
        months[name] = pipeline.monthly_percentages(frame.lazy()).filter(pl.col("muestras") >= min_samples).collect()
    rows = []
    for real, est in zip(months["real"].iter_rows(named=True), months["proyeccion"].iter_rows(named=True)):
        a = lectura.traffic_light(real[c.COL_PCT_SOL], real[c.COL_PCT_TIERRA])
        b = lectura.traffic_light(est[c.COL_PCT_SOL], est[c.COL_PCT_TIERRA])
        rows.append({"mes": real[c.COL_UTC].strftime("%Y-%m"), "pct_sol_real": _clean(real[c.COL_PCT_SOL]),
                     "pct_sol_proy": _clean(est[c.COL_PCT_SOL]), "pct_tierra_real": _clean(real[c.COL_PCT_TIERRA]),
                     "pct_tierra_proy": _clean(est[c.COL_PCT_TIERRA]),
                     "acierta": {k: a[k] == b[k] for k in (c.KEY_ENERGIA, c.KEY_COMUNICACION, c.KEY_GLOBAL)}})
    total = len(rows)
    return {"meses": total, "meses_detalle": rows,
            **{f"aciertos_{k}": sum(r["acierta"][k] for r in rows) for k in (c.KEY_ENERGIA, c.KEY_COMUNICACION,
                                                                                c.KEY_GLOBAL)}}


def evaluate_site(training: pl.LazyFrame, truth_lazy: pl.LazyFrame) -> dict:
    """Ajusta con `training` y mide el error frente a `truth_lazy` (misma estructura que las efemérides)."""
    model = projection.fit_projection(training)
    truth = truth_lazy.select(c.COL_UTC, *VALUE_COLUMNS).collect()
    times = truth[c.COL_UTC].to_list()
    pred = model.predict(times)
    baseline = projection.persistence_baseline(training, times)
    errors = error_by_month(truth, pred, model.last_t_days, model.t0)
    for column in VALUE_COLUMNS:
        errors[column]["rmse_referencia_ingenua_deg"] = _clean(np.sqrt(np.mean(
            _error(column, baseline[column], truth[column].to_numpy()) ** 2)))
    return {"periodo_sinodico_dias": _clean(model.synodic_days), "ventana_dias": _clean(model.window_days),
            "errores": errors,
            "puestas_sol": event_errors(truth, pred, c.COL_SOL_EL, SOL_VISIBLE),
            "puestas_tierra": event_errors(truth, pred, c.COL_TIERRA_EL, TIERRA_VISIBLE),
            "semaforo": semaforo_hits(truth, pred, pipeline.step_hours(truth_lazy))}

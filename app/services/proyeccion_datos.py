"""Proyección hasta jul-2028 para administradores: lee data/proyecciones/ y calcula métricas mensuales con las mismas
utilidades que las métricas reales. Nunca inventa valores: sin archivo, la serie va vacía y se explica en `limitaciones`."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.core import config as c
from app.services import lectura
from app.services.lectura import LecturaError, Row

STEP_TOL_H = 1e-9


def _dir() -> Path:
    return c.get_data_dir() / c.PROYECCION_DIR


def parse_step(value: str | None) -> int:
    """`paso_horas` de la serie (1, 6 o 24; por defecto 6). Otro valor: 422 PASO_HORAS_INVALIDO."""
    if value is None:
        return c.PROYECCION_PASO_DEFECTO_H
    try:
        step = int(value)
    except ValueError:
        step = None
    if step not in c.PROYECCION_PASOS_H:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_PROJ_STEP.format(opciones=", ".join(map(str, c.PROYECCION_PASOS_H))))
    return step


def _load_rows(path: Path) -> list[Row]:
    """Filas tipadas del CSV de proyección, por nombre de columna (las columnas extra se ignoran). Orden cronológico,
    paso constante y sin huecos, o 503."""
    invalid = LecturaError(c.HTTP_UNAVAILABLE, c.MSG_PROJ_INVALID)
    try:
        raw = pl.read_csv(path, infer_schema=False)
        if not set(c.PROYECCION_COLUMNAS) <= set(raw.columns) or raw.height < 2:
            raise invalid
        frame = raw.select(
            pl.col(c.COL_UTC).str.strptime(pl.Datetime("us"), c.PROYECCION_UTC_FORMAT, strict=True).dt.replace_time_zone("UTC"),
            *[pl.col(col).cast(pl.Float64, strict=True) for col in c.PROYECCION_COLUMNAS[1:]])
    except LecturaError:
        raise
    except (pl.exceptions.PolarsError, UnicodeDecodeError, OSError):
        raise invalid from None
    if frame.null_count().sum_horizontal().item() or not all(frame[col].is_finite().all() for col in c.PROYECCION_COLUMNAS[1:]):
        raise invalid
    steps = frame[c.COL_UTC].diff().drop_nulls().unique()
    if steps.len() != 1 or steps[0] <= timedelta(0):
        raise invalid
    return frame.to_dicts()


def site_rows(site_id: str) -> list[Row] | None:
    """Filas proyectadas del sitio, o None si no hay archivo. 404 si el sitio no está en el catálogo."""
    lectura.require_site(site_id)
    return lectura.cached_by_mtime(_dir() / c.PROYECCION_FILENAME.format(site_id=site_id), _load_rows, c.KEY_SERIE)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _model_from(raw: dict) -> dict:
    """Objeto `modelo` del contrato a partir de modelo.json. Tolerante: lo que falta o no es del tipo esperado va null."""
    error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
    rmse = error.get("rmse_deg") if isinstance(error.get("rmse_deg"), dict) else raw.get("rmse_deg")
    rmse = rmse if isinstance(rmse, dict) else {}
    horizon_raw = error.get("por_horizonte")
    horizon = [{"meses": _number(h.get("meses")), "rmse_sol_el_deg": _number(h.get("rmse_sol_el_deg")),
                "rmse_tierra_el_deg": _number(h.get("rmse_tierra_el_deg"))}
               for h in horizon_raw if isinstance(h, dict)] if isinstance(horizon_raw, list) else None
    control_raw = error.get("control_horizons")
    control = [{"site_id": _text(h.get("site_id")), "utc": _text(h.get("utc")), "variable": _text(h.get("variable")),
                "error_deg": _number(h.get("error_deg"))}
               for h in control_raw if isinstance(h, dict)] if isinstance(control_raw, list) else None
    hits = error.get("aciertos_semaforo_control", raw.get("aciertos_semaforo_control"))
    hits = hits if isinstance(hits, dict) else None
    return {
        "version": _text(raw.get("version")), "metodo": _text(raw.get("metodo")), "entrenado_desde": _text(raw.get("entrenado_desde")),
        "entrenado_hasta": _text(raw.get("entrenado_hasta")), "proyectado_hasta": _text(raw.get("proyectado_hasta")),
        "error": {
            "rmse_deg": {k: _number(rmse.get(k, rmse.get(k.removesuffix("_deg")))) for k in
                         ("sol_el_deg", "tierra_el_deg", "sol_az_deg", "tierra_az_deg")},
            "por_horizonte": horizon, "control_horizons": control,
            "aciertos_semaforo_control": {"aciertos": _number(hits.get("aciertos")), "total": _number(hits.get("total"))} if hits else None,
        },
    }


def _empty_model() -> dict:
    return _model_from({})


def load_model() -> tuple[dict, bool]:
    """(objeto modelo, disponible). Sin archivo o ilegible: todo null y disponible=False."""
    def loader(path: Path) -> dict | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return raw if isinstance(raw, dict) else None
    raw = lectura.cached_by_mtime(_dir() / c.PROYECCION_MODELO_FILENAME, loader, c.KEY_MODELO)
    return (_model_from(raw), True) if raw else (_empty_model(), False)


def _months(rows: list[Row], start: datetime, end: datetime, step_h: float) -> list[dict]:
    """Métricas de cada mes natural dentro de [start, end] (el último mes puede ser parcial)."""
    out, cursor = [], start
    last_exclusive = end + timedelta(hours=step_h)
    while cursor < last_exclusive:
        nxt = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
        month_end = min(nxt, last_exclusive)
        metrics = lectura.compute_metrics("", rows, step_h, cursor, month_end)
        sem = metrics[c.KEY_SEMAFORO]
        out.append({
            c.KEY_MES: cursor.strftime(c.PROYECCION_MES_FORMAT), c.COL_PCT_SOL: metrics[c.COL_PCT_SOL],
            c.COL_PCT_TIERRA: metrics[c.COL_PCT_TIERRA],
            c.KEY_SEMAFORO: {c.KEY_ENERGIA: sem[c.KEY_ENERGIA], c.KEY_COMUNICACION: sem[c.KEY_COMUNICACION]},
            c.KEY_INDICADORES: metrics[c.KEY_INDICADORES],
        })
        cursor = month_end
    return out


def _site(site_id: str, desde: str | None, hasta: str | None, paso_h: int) -> dict | None:
    rows = site_rows(site_id)
    if rows is None:
        return None
    step_h = lectura.step_hours(rows)
    start, end = lectura.resolve_range(rows, desde, hasta, max_days=None)
    if paso_h < step_h or paso_h % step_h:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_PROJ_STEP_FILE.format(paso=step_h))
    stride = round(paso_h / step_h)
    sample = [r for r in rows if start <= r[c.COL_UTC] <= end]
    if not sample:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_NO_ROWS_IN_RANGE)
    series = sample[::stride]
    if len(series) > c.PROYECCION_MAX_PUNTOS:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SIM_TOO_MANY_POINTS.format(points=len(series), max=c.PROYECCION_MAX_PUNTOS))
    return {
        c.COL_SITE_ID: site_id, c.KEY_SHA3: lectura.file_sha3(_dir() / c.PROYECCION_FILENAME.format(site_id=site_id)),
        c.KEY_SERIE: [{**{k: r[k] for k in c.PROYECCION_COLUMNAS[1:]}, c.COL_UTC: lectura.fmt(r[c.COL_UTC])} for r in series],
        c.KEY_MESES: _months(rows, sample[0][c.COL_UTC], sample[-1][c.COL_UTC], step_h),
    }


def projection(site_ids: list[str], desde: str | None, hasta: str | None, paso_horas: str | None) -> dict:
    unique = list(dict.fromkeys(site_ids))
    if not unique:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SITE_ID_REQUIRED)
    if len(unique) > c.PROYECCION_MAX_SITIOS:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_COMPARE_COUNT.format(max=c.PROYECCION_MAX_SITIOS))
    step = parse_step(paso_horas)
    results, missing = [], []
    for site_id in unique:
        site = _site(site_id, desde, hasta, step)
        if site is None:
            missing.append(site_id)
            site = {c.COL_SITE_ID: site_id, c.KEY_SHA3: None, c.KEY_SERIE: [], c.KEY_MESES: []}
        results.append(site)
    model, has_model = load_model()
    limits = [{c.KEY_CODIGO: c.LIM_NO_ES_EFEMERIDE, c.KEY_MENSAJE: c.MSG_LIM_NO_ES_EFEMERIDE},
              {c.KEY_CODIGO: c.LIM_HORIZONTE_IDEAL, c.KEY_MENSAJE: c.MSG_LIM_HORIZONTE_IDEAL}]
    if missing:
        limits.insert(0, {c.KEY_CODIGO: c.LIM_SIN_DATOS, c.KEY_MENSAJE: c.MSG_LIM_SIN_DATOS.format(sitios=", ".join(missing))})
    if not has_model:
        limits.insert(0, {c.KEY_CODIGO: c.LIM_SIN_MODELO, c.KEY_MENSAJE: c.MSG_LIM_SIN_MODELO})
    return {c.KEY_ORIGEN: c.ORIGEN_PROYECCION, c.KEY_MODELO: model, c.KEY_LIMITACIONES: limits, c.KEY_RESULTADOS: results}

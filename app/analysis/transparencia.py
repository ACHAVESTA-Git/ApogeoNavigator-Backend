"""Transparencia del análisis: cómo se usan los datos de un sitio, paso a paso y con números reales.

No recalcula nada que ya exista: reutiliza el pipeline de Polars, el espectro, el modelo ajustado (en caché por el
`mtime` del CSV, como la simulación) y la tabla de errores de la validación. Si un usuario sube efemérides nuevas,
el archivo cambia, la caché se invalida y este análisis refleja los datos nuevos; la validación contra Horizons, en
cambio, solo vale para los datos con los que se hizo, y el DTO lo dice (`vigente`).
"""
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sqlalchemy.exc import SQLAlchemyError

from app.analysis import frames, harmonic, pipeline, projection, simulacion, spectrum
from app.core import config as c
from app.db import session as db_session
from app.db.models import CargaArchivo
from app.schemas import analisis as dto
from app.security import pqc
from app.services import lectura
from app.services.lectura import LecturaError

SECONDS_PER_DAY = c.SECONDS_PER_HOUR * c.HOURS_PER_DAY
VARIABLES = simulacion.VARIABLES
ANGLES = (c.COL_SOL_AZ, c.COL_TIERRA_AZ)
EARTH_BODY, SUN_BODY = c.KEY_TIERRA, c.KEY_SOL


def _r(value: float | None, digits: int = c.ROUND_DEG) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


# ------------------------------------------------------------------ origen de los datos
def _file_sha3(path: Path) -> str | None:
    return lectura.cached_by_mtime(path, lambda p: hashlib.sha3_256(p.read_bytes()).hexdigest(), c.CACHE_KIND_SHA3)


def _manifest_signature(manifest: dict) -> tuple[bool | None, str | None]:
    """(firmado, nota): True solo si la firma del manifiesto es VÁLIDA; None si no se puede verificar; False si no es válida.

    `firmado` no significa «tiene el campo firma»: se verifica con la clave pública del servidor y su huella.
    """
    try:
        problems = pqc.verificar_manifiesto(manifest)      # versión, algoritmo, huella y firma (los archivos van aparte: `coincide`)
    except pqc.ErrorClavesPQC:
        return None, c.MSG_MANIFEST_UNVERIFIABLE
    if problems:
        return False, c.MSG_MANIFEST_NO_VERIFY_DETAIL.format(problems="; ".join(problems))
    return True, None


def _manifest_entry(filename: str) -> dto.EntradaManifiesto | None:
    """Lo que dice el manifiesto firmado del archivo. Un manifiesto ausente o ilegible no impide el análisis."""
    def loader(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"files": {f[c.KEY_MANIFEST_NAME]: f for f in data[c.KEY_MANIFEST_FILES]},
                    "generated": data[c.KEY_MANIFEST_GENERATED], "raw": data}
        except (OSError, ValueError, KeyError, TypeError):
            return {}
    manifest = lectura.cached_by_mtime(c.get_data_dir() / c.MANIFEST_NAME, loader, c.CACHE_KIND_MANIFEST)
    entry = (manifest or {}).get("files", {}).get(filename)
    if not entry:
        return None
    signed, note = _manifest_signature(manifest["raw"])     # en cada petición: depende de la clave actual, no solo del archivo
    return dto.EntradaManifiesto(sha3_256=entry[pqc.MANIFEST_HASH_ALGORITHM], generado_utc=manifest["generated"],
                                 firmado=signed, firma_nota=note, coincide=False)


def _last_saved_load(filename: str) -> dict | None:
    """Última carga GUARDADA (no un dry-run) del archivo en cargas_archivo; None sin base de datos o sin registro."""
    if not db_session.db_enabled():
        return None
    try:
        with db_session.session_scope() as session:
            row = (session.query(CargaArchivo)
                   .filter(CargaArchivo.nombre_archivo == filename, CargaArchivo.valido.is_(True),
                           CargaArchivo.ruta_almacenamiento.is_not(None))
                   .order_by(CargaArchivo.creado_utc.desc()).first())
            if row is None:
                return None
            uploader = (c.UPLOADER_USER if row.usuario_id else c.UPLOADER_API_KEY if row.actor == c.ACTOR_API_KEY
                        else c.UPLOADER_ANONYMOUS)
            return {"when": row.creado_utc, "sha3": row.sha3_256, "name": row.nombre_archivo, "by": uploader}
    except SQLAlchemyError:
        return None


def _origin(filename: str, current_sha3: str) -> dto.Origen:
    """De dónde vienen los datos, afirmando solo lo que se puede demostrar con hashes."""
    seed_dir = c.get_seed_dir()
    seed_sha3 = _file_sha3(seed_dir / filename) if seed_dir else None
    load = _last_saved_load(filename)
    manifest = _manifest_entry(filename)
    if manifest is not None:
        manifest = manifest.model_copy(update={"coincide": manifest.sha3_256 == current_sha3})
    last = None if load is None else dto.UltimaCarga(
        cargado_utc=lectura.fmt(load["when"]), archivo=load["name"], sha3_256=load["sha3"],
        coincide=load["sha3"] == current_sha3, cargado_por=load["by"])
    if seed_sha3 == current_sha3:
        kind = c.ORIGIN_NASA_SEED
    elif last is not None and last.coincide:
        kind = c.ORIGIN_UPLOADED
    elif seed_sha3 is not None or last is not None:
        kind = c.ORIGIN_MODIFIED
    else:
        kind = c.ORIGIN_UNRECORDED
    return dto.Origen(tipo=kind, descripcion=c.DESC_ORIGIN[kind], sha3_256=current_sha3,
                      coincide_con_semilla=None if seed_sha3 is None else seed_sha3 == current_sha3,
                      ultima_carga=last, manifiesto=manifest)


# ------------------------------------------------------------------ análisis de los datos (en caché por mtime del CSV)
def _error(column: str, predicted: np.ndarray, actual: np.ndarray) -> np.ndarray:
    return harmonic.circular_error(predicted, actual) if column in ANGLES else predicted - actual


def _r_squared(column: str, observed: np.ndarray, fitted: np.ndarray) -> float | None:
    """1 − SSE/SST; en los azimuts, con diferencias angulares respecto a la media circular."""
    if column in ANGLES:
        rad = np.deg2rad(observed)
        center = np.rad2deg(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean()))
        sst = float(np.sum(harmonic.circular_error(observed, np.full_like(observed, center)) ** 2))
    else:
        sst = float(np.sum((observed - observed.mean()) ** 2))
    return None if sst == 0 else 1.0 - float(np.sum(_error(column, fitted, observed) ** 2)) / sst


def _residuals(column: str, observed: np.ndarray, fitted: np.ndarray) -> dto.Residuos:
    err = _error(column, fitted, observed)
    return dto.Residuos(rmse_deg=_r(np.sqrt(np.mean(err ** 2))), mae_deg=_r(np.mean(np.abs(err))), sesgo_deg=_r(np.mean(err)),
                        max_abs_deg=_r(np.max(np.abs(err))), p95_abs_deg=_r(np.percentile(np.abs(err), 95)))


def _spectrum(rows_obs: dict[str, np.ndarray], step_days: float, window_days: float, refined: float) -> dto.Espectro:
    series = []
    for column, name in c.SPECTRUM_SERIES:
        values = rows_obs[column]
        if column in ANGLES:
            values = np.rad2deg(np.unwrap(np.deg2rad(values)))
        peaks = spectrum.dominant_periods(values, step_days, top=c.ANALISIS_SPECTRUM_PEAKS)
        series.append(dto.EspectroSerie(variable=column, nombre=name, picos=[
            dto.Pico(periodo_dias=_r(p.period_days), amplitud_rel=_r(p.amplitude), candidatos=list(p.candidates),
                     es_sinodico="sinodico" in p.candidates) for p in peaks]))
    return dto.Espectro(ventana_dias=_r(window_days), resolucion_dias=_r(c.SYNODIC_MONTH_DAYS ** 2 / window_days),
                        sinodico_teorico_dias=c.SYNODIC_MONTH_DAYS, sinodico_afinado_dias=_r(refined),
                        series=series, nota=c.ANALISIS_SPECTRUM_NOTE)


def _terms(model: projection.SiteProjection) -> tuple[list[dto.Termino], dict[str, int]]:
    """Periodos del modelo con su papel físico, y número de términos (cos y sen) por cuerpo."""
    synodic = 1.0 / model.synodic_days
    out, counts = [], {}
    for body, terms in ((SUN_BODY, projection.sun_terms(synodic)), (EARTH_BODY, projection.earth_terms())):
        roles = {round(abs(freq), 9): role for freq, role in terms}
        frequencies = model.models[body].frequencies
        counts[c.PROJECTION_BODY_NAMES[body]] = 2 * len(frequencies)
        out += [dto.Termino(cuerpo=c.PROJECTION_BODY_NAMES[body], periodo_dias=_r(1.0 / f), rol=roles.get(f, c.ROLE_FALLBACK))
                for f in frequencies]
    if model.sun_elevation_direct is not None:
        counts[c.PROJECTION_BODY_NAMES[SUN_BODY]] += 2 * len(model.sun_elevation_direct.frequencies)
    return sorted(out, key=lambda t: -t.periodo_dias), counts


def _features(frame, step_h: float) -> dict[str, Any]:
    visible = pipeline.with_visibility(frame)
    wins = pipeline.windows(visible, frames.SOL_VISIBLE).filter(
        pl.col(pipeline.WINDOW_COMPLETE) & pl.col(pipeline.WINDOW_VISIBLE)).collect()
    days = pipeline.lunar_day_stats(frame, step_h).collect()
    full = days.filter(pl.col(c.COL_SAMPLES) >= 0.9 * c.LUNAR_DAY_DAYS * c.HOURS_PER_DAY / step_h)
    lib = pipeline.libration(frame).collect().row(0, named=True)
    return {"sun_windows": wins.height, "sun_window_mean": wins[pipeline.WINDOW_HOURS].mean() if wins.height else None,
            "lunar_days": full.height, "sun_hours_day": full[c.COL_HORAS_SOL].mean() if full.height else None,
            "lib_az": lib[c.COL_LIB_AZ_RANGE], "lib_el": lib[c.COL_LIB_EL_RANGE]}


def _analyse(site_id: str, rows: list[lectura.Row], path: Path) -> dict[str, Any]:
    """Todo lo que depende solo del archivo (no de los parámetros de la petición)."""
    frame = frames.from_rows(rows)
    step_h = pipeline.step_hours(frame)
    model = simulacion.site_model(site_id, rows)           # reutiliza el modelo en caché: no se reajusta
    first, last = rows[0][c.COL_UTC], rows[-1][c.COL_UTC]
    observed = {col: np.array([r[col] for r in rows], dtype=float) for col in VARIABLES}
    t_days = model.days_of([r[c.COL_UTC] for r in rows])
    fitted = model.predict_days(t_days)
    gaps = int(round((last - first).total_seconds() / (step_h * c.SECONDS_PER_HOUR))) + 1 - len(rows)
    terms, counts = _terms(model)
    return {
        "frame_features": _features(frame, step_h), "step_h": step_h, "first": first, "last": last, "gaps": gaps,
        "bytes": path.stat().st_size, "sha3": _file_sha3(path), "model": model,
        "observed": observed, "fitted": fitted, "t_days": t_days,
        "r2": {col: _r(_r_squared(col, observed[col], fitted[col])) for col in VARIABLES},
        "residuals": {col: _residuals(col, observed[col], fitted[col]) for col in VARIABLES},
        "spectrum": _spectrum(observed, step_h / c.HOURS_PER_DAY, model.window_days, model.synodic_days),
        "terms": terms, "term_counts": counts, "sun_el_max": float(observed[c.COL_SOL_EL].max()), "rows": len(rows),
    }


# ------------------------------------------------------------------ validación contra Horizons
def _validation(site_id: str, current_sha3: str) -> dto.Validacion:
    empty = dict(vigente=None, validacion_desde_utc=None, validacion_hasta_utc=None, filas_entrenamiento_validadas=None, rmse_deg={},
                 mae_deg={}, rmse_referencia_ingenua_deg={}, mejora_frente_a_ingenua={}, rmse_por_mes_deg={},
                 semaforo_aciertos={}, puestas_sol={})
    try:
        table = simulacion.error_table(site_id)
        meta = simulacion.error_meta()
    except LecturaError as exc:
        return dto.Validacion(disponible=False, motivo=exc.detail, **empty)
    summary = (table or {}).get(c.KEY_SUMMARY)
    if not table or not summary:
        return dto.Validacion(disponible=False, motivo=c.MSG_ANALYSIS_NO_VALIDATION, **empty)
    period = meta.get(c.KEY_VALIDATION_PERIOD) or {}
    rmse = {col: table[col][c.KEY_RMSE_DEG] for col in VARIABLES}
    naive = {col: table[col].get(c.KEY_RMSE_NAIVE) for col in VARIABLES}
    return dto.Validacion(
        disponible=True, vigente=summary.get(c.KEY_DATA_SHA3) == current_sha3, motivo=None,
        validacion_desde_utc=period.get(c.KEY_START), validacion_hasta_utc=period.get(c.KEY_END),
        filas_entrenamiento_validadas=summary.get(c.KEY_TRAINING, {}).get(c.KEY_ROWS), rmse_deg=rmse,
        mae_deg={col: table[col][c.KEY_MAE_DEG] for col in VARIABLES}, rmse_referencia_ingenua_deg=naive,
        mejora_frente_a_ingenua={col: _r(naive[col] / rmse[col]) if naive[col] and rmse[col] else None for col in VARIABLES},
        rmse_por_mes_deg={col: table[col][c.KEY_RMSE_MONTHS] for col in VARIABLES},
        semaforo_aciertos=summary.get(c.KEY_TRAFFIC_HITS, {}),
        puestas_sol={k: v for k, v in summary.get(c.KEY_EVENTS_SUN, {}).items()
                     if k in (c.KEY_EVENTS_REAL, c.KEY_EVENTS_MATCHED_PCT, c.KEY_EVENTS_MAE_H)})


def _limitations(sun_el_max: float, window_days: float, validation: dto.Validacion) -> list[dto.Limitacion]:
    flags = {c.LIMIT_IDEAL_HORIZON: True, c.LIMIT_NOT_EPHEMERIS: True,
             c.LIMIT_SHORT_WINDOW: window_days < c.ECLIPSE_YEAR_DAYS, c.LIMIT_NOT_BLIND: True,
             c.LIMIT_POLAR_SUN: sun_el_max < c.RELIEF_WARNING_MAX_SUN_EL_DEG,
             c.LIMIT_ZENITH: sun_el_max > c.SIM_AZIMUTH_MAX_SUN_EL_DEG,
             c.LIMIT_STALE_VALIDATION: validation.disponible and validation.vigente is False}
    return [dto.Limitacion(codigo=code, mensaje=c.LIMITS_TEXT[code].format(days=window_days, year=c.ECLIPSE_YEAR_DAYS),
                           aplica=applies) for code, applies in flags.items()]


# ------------------------------------------------------------------ serie para graficar
def _series(a: dict, rows: list[lectura.Row], desde: str | None, hasta: str | None, variable: str) -> tuple[dto.Serie, datetime, datetime]:
    model: projection.SiteProjection = a["model"]
    first, last, step_h = a["first"], a["last"], a["step_h"]
    start = lectura.parse_instant(c.PARAM_DESDE, desde) or first
    end = lectura.parse_instant(c.PARAM_HASTA, hasta) or min(last + timedelta(days=c.ANALISIS_DEFAULT_PROJECTION_DAYS),
                                                           last + timedelta(days=c.PROJECTION_MAX_DAYS))
    if start < first:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_ANALYSIS_BEFORE_DATA)
    if end <= start:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_RANGE_ORDER)
    if end > last + timedelta(days=c.PROJECTION_MAX_DAYS):
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_SIM_BEYOND_PROJECTION.format(days=c.PROJECTION_MAX_DAYS))
    span_h = (end - start).total_seconds() / c.SECONDS_PER_HOUR
    step = max(1, math.ceil(span_h / step_h / c.ANALISIS_SERIES_POINTS)) * step_h      # múltiplo del paso de los datos
    count = int(span_h / step) + 1
    times = [start + timedelta(hours=step * i) for i in range(count)]
    t_days = model.days_of(times)
    inside = t_days <= model.last_t_days
    real = simulacion.real_series(rows, t_days, model.t0)[variable]
    predicted = model.predict_days(t_days)[variable]
    points = [dto.PuntoSerie(utc=lectura.fmt(t), observado=_r(real[i]) if inside[i] else None,
                             ajustado=_r(predicted[i]) if inside[i] else None,
                             proyectado=None if inside[i] else _r(predicted[i])) for i, t in enumerate(times)]
    return dto.Serie(variable=variable, nombre=c.ANALISIS_VARIABLE_NAMES[variable], paso_horas=step,
                     fin_datos_utc=lectura.fmt(last), puntos=points), start, end


# ------------------------------------------------------------------ pasos del pipeline
def _m(name: str, value, unit: str = c.UNIT_NONE) -> dto.Medida:
    return dto.Medida(nombre=name, valor=_r(value) if isinstance(value, float) else value, unidad=unit)


def _steps(a: dict, validation: dto.Validacion, entrada: dto.Entrada) -> list[dto.Paso]:
    f, spec, model = a["frame_features"], a["spectrum"], a["model"]
    sun_peak = next((p for p in spec.series[0].picos if p.es_sinodico), None)
    mean_r2 = float(np.mean([v for v in a["r2"].values() if v is not None])) if any(v is not None for v in a["r2"].values()) else None
    fit_rmse = float(np.mean([r.rmse_deg for r in a["residuals"].values()]))
    ok, no = c.STEP_OK, c.STEP_UNAVAILABLE
    outcomes = [
        (ok, [_m(c.LBL_ROWS, a["rows"], c.UNIT_ROWS), _m(c.LBL_BYTES, a["bytes"], c.UNIT_BYTES),
              _m(c.LBL_RANGE_DAYS, (a["last"] - a["first"]).total_seconds() / SECONDS_PER_DAY, c.UNIT_DAYS),
              _m(c.LBL_STEP_H, a["step_h"], c.UNIT_HOURS)]),
        (ok, [_m(c.LBL_VALID_ROWS, entrada.calidad.filas_validas, c.UNIT_ROWS),
              _m(c.LBL_ERROR_ROWS, entrada.calidad.filas_con_error, c.UNIT_ROWS), _m(c.LBL_GAPS, entrada.calidad.huecos, c.UNIT_ROWS)]),
        (ok, [_m(c.LBL_SUN_WINDOWS, f["sun_windows"]), _m(c.LBL_SUN_WINDOW_MEAN, _r(f["sun_window_mean"], 1), c.UNIT_HOURS),
              _m(c.LBL_LUNAR_DAYS, f["lunar_days"]), _m(c.LBL_SUN_HOURS_DAY, _r(f["sun_hours_day"], 1), c.UNIT_HOURS),
              _m(c.LBL_LIB_AZ, _r(f["lib_az"], 1), c.UNIT_DEG), _m(c.LBL_LIB_EL, _r(f["lib_el"], 1), c.UNIT_DEG)]),
        (ok if sun_peak else c.STEP_WARNING, [
            _m(c.LBL_SYNODIC_PEAK, sun_peak.periodo_dias if sun_peak else None, c.UNIT_DAYS),
            _m(c.LBL_PEAK_PERIOD, spec.series[0].picos[0].periodo_dias if spec.series[0].picos else None, c.UNIT_DAYS),
            _m(c.LBL_RESOLUTION, spec.resolucion_dias, c.UNIT_DAYS)]),
        (ok, [_m(c.LBL_SYNODIC_REFINED, spec.sinodico_afinado_dias, c.UNIT_DAYS), _m(c.LBL_TERMS, sum(a["term_counts"].values()), c.UNIT_TERMS),
              _m(c.LBL_R2_MEAN, _r(mean_r2)), _m(c.LBL_RMSE_FIT, _r(fit_rmse), c.UNIT_DEG)]),
    ]
    if validation.disponible:
        hits = validation.semaforo_aciertos
        state = ok if validation.vigente else c.STEP_WARNING
        by_month = validation.rmse_por_mes_deg[c.COL_SOL_EL]
        outcomes += [
            (state, [_m(c.LBL_RMSE_SUN_EL, validation.rmse_deg[c.COL_SOL_EL], c.UNIT_DEG),
                     _m(c.LBL_RMSE_EARTH_AZ, validation.rmse_deg[c.COL_TIERRA_AZ], c.UNIT_DEG),
                     _m(c.LBL_TRAFFIC_HITS, f"{hits.get(c.KEY_HITS_PREFIX + c.KEY_GLOBAL)} de {hits.get(c.KEY_MONTHS)}", c.UNIT_MONTHS),
                     _m(c.LBL_VALID_STALE, bool(validation.vigente))]),
            (state, [_m(c.LBL_PROJECTABLE, c.PROJECTION_MONTHS_AHEAD, c.UNIT_MONTHS),
                     _m(c.LBL_UNC_FIRST, by_month[0], c.UNIT_DEG), _m(c.LBL_UNC_LAST, by_month[-1], c.UNIT_DEG)])]
    else:
        outcomes += [(no, [_m(c.LBL_VALID_STALE, False)]), (no, [_m(c.LBL_PROJECTABLE, c.PROJECTION_MONTHS_AHEAD, c.UNIT_MONTHS)])]
    ids = [step[0] for step in c.ANALISIS_STEPS]
    if not validation.disponible:
        texts = (c.DESC_VALIDATION_MISSING, c.DESC_PROJECTION_MISSING)
    elif validation.vigente:
        texts = (c.DESC_VALIDATION_CURRENT, c.DESC_PROJECTION_CURRENT)
    else:
        texts = (c.DESC_VALIDATION_STALE, c.DESC_PROJECTION_STALE)
    override = dict(zip(ids[-2:], texts))
    return [dto.Paso(id=sid, nombre=name, descripcion=override.get(sid, desc), estado=state, resultado=measures)
            for (sid, name, desc), (state, measures) in zip(c.ANALISIS_STEPS, outcomes, strict=True)]


# ------------------------------------------------------------------ punto de entrada
def analyse(site_id: str, desde: str | None, hasta: str | None, variable: str | None) -> dto.AnalisisOut:
    variable = variable or c.ANALISIS_DEFAULT_VARIABLE
    if variable not in VARIABLES:
        raise LecturaError(c.HTTP_UNPROCESSABLE, c.MSG_ANALYSIS_VARIABLE.format(values=", ".join(VARIABLES)))
    rows = lectura.ephemeris(site_id)                       # 404 si el sitio o el archivo no existen; 503 si no validan
    path = c.get_data_dir() / c.EFEMERIDES_FILENAME.format(site_id=site_id)
    a = lectura.cached_by_mtime(path, lambda p: _analyse(site_id, rows, p), c.CACHE_KIND_ANALYSIS)
    filename = path.name
    entrada = dto.Entrada(
        archivo=filename, filas=a["rows"], bytes=a["bytes"], inicio_utc=lectura.fmt(a["first"]), fin_utc=lectura.fmt(a["last"]),
        paso_horas=a["step_h"],
        calidad=dto.Calidad(filas_validas=a["rows"], filas_con_error=0, huecos=a["gaps"], paso_constante=a["gaps"] == 0),
        origen=_origin(filename, a["sha3"]))
    validation = _validation(site_id, a["sha3"])
    serie, start, end = _series(a, rows, desde, hasta, variable)
    model = dto.Modelo(terminos_por_cuerpo=a["term_counts"], periodos=a["terms"],
                       ajuste_directo_elevacion_sol=a["model"].sun_elevation_direct is not None, r2=a["r2"],
                       residuos=a["residuals"], nota_ajuste=c.NOTE_FIT_IN_SAMPLE)
    return dto.AnalisisOut(site_id=site_id, desde_utc=lectura.fmt(start), hasta_utc=lectura.fmt(end),
                           pasos=_steps(a, validation, entrada), entrada=entrada, espectro=a["spectrum"], modelo=model,
                           validacion=validation, limitaciones=_limitations(a["sun_el_max"], a["model"].window_days, validation), serie=serie)

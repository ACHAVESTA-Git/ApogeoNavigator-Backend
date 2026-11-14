#!/usr/bin/env python3
"""Análisis exploratorio, validación de la proyección contra Horizons y gráficos.

Escribe:
  * data/proyeccion_errores.json              errores por sitio y mes de horizonte (los usa la API)
  * scripts/datos/validacion/resultados.json  resultados completos (exploración + validación)
  * docs/datos/img/*.png                      gráficos del informe (matplotlib, solo en este script)

Uso (desde la raíz del backend):  python scripts/datos/analizar_proyeccion.py
Requiere scripts/datos/validacion/horizons_verdad.parquet (python scripts/datos/descargar_validacion.py).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))

import matplotlib                                             # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                               # noqa: E402
import numpy as np                                            # noqa: E402
import polars as pl                                           # noqa: E402

from app.analysis import frames, harmonic, pipeline, projection, spectrum, validation   # noqa: E402
from app.core import config as c                              # noqa: E402
from app.services import lectura                              # noqa: E402

PARQUET = ROOT / "scripts/datos/validacion/horizons_verdad.parquet"
RESULTS = ROOT / "scripts/datos/validacion/resultados.json"
ERRORS = ROOT / "data" / c.ERRORS_FILENAME
IMG = ROOT / "docs/datos/img"
DPI = 130
COLORS = {"sol": "#d97706", "tierra": "#2563eb", "real": "#111827", "proy": "#dc2626", "ingenua": "#6b7280"}


def site_ids() -> list[str]:
    return [line.split(",")[0] for line in (ROOT / "data/sites.csv").read_text(encoding="utf-8").splitlines()[1:]]


def exploration(frame: pl.LazyFrame) -> dict:
    step = pipeline.step_hours(frame)
    visible = pipeline.with_visibility(frame)
    out: dict = {"paso_h": step}
    for body, column in (("sol", "sol_visible"), ("tierra", "tierra_visible")):
        wins = pipeline.windows(visible, column).collect()
        complete = wins.filter(pl.col(pipeline.WINDOW_COMPLETE))
        for state, name in ((True, "visibles"), (False, "oscuras")):
            durations = complete.filter(pl.col(pipeline.WINDOW_VISIBLE) == state)[pipeline.WINDOW_HOURS]
            out[f"{body}_{name}"] = {"tramos_completos": durations.len(),
                                      "duracion_media_h": round(durations.mean(), 1) if durations.len() else None,
                                      "duracion_min_h": durations.min(), "duracion_max_h": durations.max()}
    days = pipeline.lunar_day_stats(frame, step).collect()
    full = days.filter(pl.col("muestras") >= 0.9 * c.LUNAR_DAY_DAYS * 24 / step)
    out["dias_lunares_completos"] = full.height
    out["horas_sol_por_dia_lunar_media"] = round(full[c.COL_HORAS_SOL].mean(), 1) if full.height else None
    out["sol_el_max_por_dia_lunar"] = [round(v, 2) for v in full[c.COL_SOL_EL_MAX].to_list()]
    daily = pipeline.daily_extremes(frame).collect()
    out["sol_el_max_diaria"] = {"min": round(daily["sol_el_max"].min(), 3), "max": round(daily["sol_el_max"].max(), 3)}
    out["libracion"] = {k: round(v, 2) for k, v in pipeline.libration(frame).collect().row(0, named=True).items()}
    y = frame.select(c.COL_SOL_EL, c.COL_TIERRA_EL, c.COL_TIERRA_AZ, c.COL_SOL_AZ).collect()
    step_days = step / c.HOURS_PER_DAY
    out["picos_fft"] = {
        col: [{"periodo_dias": round(p.period_days, 2), "amplitud_rel": round(p.amplitude, 2),
               "candidatos": list(p.candidates)} for p in spectrum.dominant_periods(y[col].to_numpy(), step_days, 3)]
        for col in (c.COL_SOL_EL, c.COL_TIERRA_EL, c.COL_TIERRA_AZ)}
    return out


# ------------------------------------------------------------------ gráficos
def fig_windows(data: dict[str, pl.LazyFrame]) -> None:
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for row, (sid, frame) in enumerate(data.items()):
        for k, (column, color, label) in enumerate((("sol_visible", COLORS["sol"], "Sol"),
                                                    ("tierra_visible", COLORS["tierra"], "Tierra"))):
            wins = pipeline.windows(pipeline.with_visibility(frame), column).filter(
                pl.col(pipeline.WINDOW_VISIBLE)).collect()
            for w in wins.iter_rows(named=True):
                ax.barh(row + (0.2 if k == 0 else -0.2), (w["fin"] - w["inicio"]).total_seconds() / 86400,
                        left=matplotlib.dates.date2num(w["inicio"]), height=0.35, color=color,
                        label=label if row == 0 and w is wins.row(0, named=True) else None)
    ax.set_yticks(range(len(data)), list(data))
    ax.xaxis_date(); fig.autofmt_xdate()
    ax.set_title("Ventanas de luz solar y de Tierra visible (horizonte ideal, dic 2026 – may 2027)")
    handles = [matplotlib.patches.Patch(color=COLORS["sol"], label="Sol sobre el horizonte"),
               matplotlib.patches.Patch(color=COLORS["tierra"], label="Tierra sobre el horizonte")]
    ax.legend(handles=handles, loc="upper right"); fig.tight_layout(); fig.savefig(IMG / "ventanas.png", dpi=DPI); plt.close(fig)


def fig_spectrum(frame: pl.LazyFrame) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    data = frame.collect()
    for ax, (col, title) in zip(axes, ((c.COL_SOL_EL, "Elevación del Sol (Apolo 15)"), (c.COL_TIERRA_EL, "Elevación de la Tierra"),
                                       (c.COL_TIERRA_AZ, "Azimut de la Tierra"))):
        values = data[col].to_numpy()
        if col == c.COL_TIERRA_AZ:
            values = np.rad2deg(np.unwrap(np.deg2rad(values)))
        freqs, amp = spectrum.periodogram(values, 1 / c.HOURS_PER_DAY)
        mask = (freqs > 1 / 120) & (freqs < 1 / 5)
        ax.plot(1 / freqs[mask], amp[mask] / amp[mask].max(), color="#111827")
        for name, period in c.KNOWN_PERIODS_DAYS.items():
            if period < 120:
                ax.axvline(period, ls=":", lw=1, label=f"{name} {period:.2f} d")
        ax.set_xlim(5, 60); ax.set_xlabel("periodo (días)"); ax.set_title(title, fontsize=10); ax.legend(fontsize=7)
    axes[0].set_ylabel("amplitud relativa"); fig.tight_layout(); fig.savefig(IMG / "espectro.png", dpi=DPI); plt.close(fig)


def fig_season(frame: pl.LazyFrame, truth: pl.LazyFrame, model: projection.SiteProjection) -> None:
    train_d = pipeline.daily_extremes(frame).collect()
    truth_d = pipeline.daily_extremes(truth).collect()
    times = truth.select(c.COL_UTC).collect()[c.COL_UTC].to_list()
    pred = model.predict(times).lazy()
    pred_d = pipeline.daily_extremes(pred).collect()
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(train_d["fecha"], train_d["sol_el_max"], color=COLORS["real"], label="Horizons (entrenamiento, 6 meses)")
    ax.plot(truth_d["fecha"], truth_d["sol_el_max"], color=COLORS["real"], alpha=0.5, label="Horizons (verdad, 12 meses)")
    ax.plot(pred_d["fecha"], pred_d["sol_el_max"], color=COLORS["proy"], label="Proyección")
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_ylabel("elevación solar máxima del día (°)"); ax.legend(fontsize=8)
    ax.set_title("Shackleton: estacionalidad por la inclinación del eje (~1,5°)"); fig.tight_layout()
    fig.savefig(IMG / "estacionalidad_polo.png", dpi=DPI); plt.close(fig)


def fig_libration(data: dict[str, pl.LazyFrame]) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(13, 3.6))
    for ax, sid in zip(axes, ("apollo11", "apollo15", "shackleton", "slim")):
        d = data[sid].collect()
        az = harmonic.circular_error(d[c.COL_TIERRA_AZ].to_numpy(), np.rad2deg(np.arctan2(
            np.sin(np.deg2rad(d[c.COL_TIERRA_AZ].to_numpy())).mean(), np.cos(np.deg2rad(d[c.COL_TIERRA_AZ].to_numpy())).mean())))
        sc = ax.scatter(az, d[c.COL_TIERRA_EL].to_numpy(), c=d["t_dias"].to_numpy(), s=3, cmap="viridis")
        ax.set_title(sid, fontsize=10); ax.set_xlabel("azimut − media (°)")
    axes[0].set_ylabel("elevación de la Tierra (°)")
    fig.colorbar(sc, ax=axes, label="días", shrink=0.8); fig.suptitle("Libración: la Tierra «baila» en el cielo", y=1.02)
    fig.savefig(IMG / "libracion.png", dpi=DPI, bbox_inches="tight"); plt.close(fig)


def fig_overlay(frame: pl.LazyFrame, truth: pl.LazyFrame, model: projection.SiteProjection) -> None:
    truth_df = truth.collect()
    times = truth_df[c.COL_UTC].to_list()
    pred = model.predict(times)
    base = projection.persistence_baseline(frame, times)
    sl = slice(len(times) - 24 * 20, len(times))        # últimos 20 días de la proyección (mes 12)
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True)
    for ax, col, title in ((axes[0], c.COL_SOL_EL, "Elevación del Sol"), (axes[1], c.COL_TIERRA_AZ, "Azimut de la Tierra")):
        ax.plot(times[sl], truth_df[col].to_numpy()[sl], color=COLORS["real"], label="Horizons (verdad)")
        ax.plot(times[sl], pred[col].to_numpy()[sl], color=COLORS["proy"], label="Proyección armónica")
        ax.plot(times[sl], base[col][sl], color=COLORS["ingenua"], ls="--", label="Repetir el último ciclo")
        ax.set_ylabel(f"{title} (°)"); ax.legend(fontsize=8)
    axes[0].set_title("Apolo 15 · últimos 20 días de la proyección (12 meses después del último dato)")
    fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(IMG / "proyeccion_vs_verdad.png", dpi=DPI); plt.close(fig)


def fig_errors(results: dict) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(14, 3.6), sharex=True)
    months = np.arange(1, c.PROJECTION_MONTHS_AHEAD + 1)
    for ax, col in zip(axes, (c.COL_SOL_AZ, c.COL_SOL_EL, c.COL_TIERRA_AZ, c.COL_TIERRA_EL)):
        for sid, r in results.items():
            ax.plot(months, [v if v is not None else np.nan for v in r["errores"][col]["rmse_por_mes_deg"]], lw=1, alpha=0.7)
        ax.set_title(col.replace("_deg", ""), fontsize=10); ax.set_xlabel("meses tras el último dato")
        if col == c.COL_SOL_AZ:
            ax.set_yscale("log")
    axes[0].set_ylabel("RMSE (°)"); fig.suptitle("Error de la proyección por mes de horizonte (una línea por sitio)", y=1.03)
    fig.tight_layout(); fig.savefig(IMG / "error_por_mes.png", dpi=DPI, bbox_inches="tight"); plt.close(fig)


def fig_traffic(results: dict) -> None:
    fig, ax = plt.subplots(figsize=(9, 3.6))
    sids = list(results)
    ax.bar(sids, [results[s]["semaforo"]["aciertos_global"] for s in sids], color="#16a34a")
    ax.axhline(c.PROJECTION_MONTHS_AHEAD, color="gray", ls=":")
    ax.set_ylabel("meses acertados (de 12)"); ax.set_title("Semáforo global proyectado frente al real")
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right"); fig.tight_layout()
    fig.savefig(IMG / "semaforo_aciertos.png", dpi=DPI); plt.close(fig)


# ------------------------------------------------------------------ principal
def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    sites = site_ids()
    train = {sid: frames.from_rows(lectura.ephemeris(sid)) for sid in sites}
    truth = {sid: frames.from_parquet(PARQUET, sid) for sid in sites}
    results: dict[str, dict] = {}
    for sid in sites:
        print(sid, flush=True)
        results[sid] = {"exploracion": exploration(train[sid]), **validation.evaluate_site(train[sid], truth[sid])}
        results[sid]["sol_elevacion_ajuste_directo"] = projection.fit_projection(train[sid]).sun_elevation_direct is not None
    first = train[sites[0]].select(c.COL_UTC).collect()[c.COL_UTC]
    last_truth = truth[sites[0]].select(c.COL_UTC).collect()[c.COL_UTC]
    meta = {"generado_utc": datetime.now(timezone.utc).strftime(c.UTC_FORMAT),
            "entrenamiento": {"inicio": first[0].strftime(c.UTC_FORMAT), "fin": first[-1].strftime(c.UTC_FORMAT)},
            "validacion": {"inicio": last_truth[0].strftime(c.UTC_FORMAT), "fin": last_truth[-1].strftime(c.UTC_FORMAT)}}
    RESULTS.write_text(json.dumps({**meta, "sitios": results}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    table = {sid: {col: {k: v for k, v in r["errores"][col].items() if k in ("mae_deg", "rmse_deg", "rmse_por_mes_deg")}
                   for col in r["errores"]} for sid, r in results.items()}
    ERRORS.write_text(json.dumps({"version": c.ERRORS_VERSION, **meta, "sitios": table}, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    fig_windows(train); fig_spectrum(train["apollo15"])
    fig_season(train["shackleton"], truth["shackleton"], projection.fit_projection(train["shackleton"]))
    fig_libration(train)
    fig_overlay(train["apollo15"], truth["apollo15"], projection.fit_projection(train["apollo15"]))
    fig_errors(results); fig_traffic(results)
    print(f"Listo: {ERRORS.relative_to(ROOT)}, {RESULTS.relative_to(ROOT)} y {len(list(IMG.glob('*.png')))} gráficos")


if __name__ == "__main__":
    main()

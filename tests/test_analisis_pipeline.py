"""Pipeline exploratorio con series sintéticas conocidas."""
import math
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from app.analysis import frames, pipeline
from app.core import config as c

START = datetime(2026, 12, 1, tzinfo=timezone.utc)


def build(hours: int, sun_el, earth_el=lambda i: 10.0, earth_az=lambda i: 200.0) -> pl.LazyFrame:
    rows = [{c.COL_UTC: START + timedelta(hours=i), c.COL_SOL_AZ: 90.0, c.COL_SOL_EL: sun_el(i),
             c.COL_TIERRA_AZ: earth_az(i) % 360, c.COL_TIERRA_EL: earth_el(i)} for i in range(hours)]
    return frames.from_rows(rows)


def square(i: int) -> float:                  # 12 h de Sol y 12 h de noche, cada día
    return 1.0 if i % 24 < 12 else -1.0


def test_paso_en_horas():
    assert pipeline.step_hours(build(48, square)) == 1.0


def test_ventanas_de_luz():
    wins = pipeline.windows(pipeline.with_visibility(build(24 * 5, square)), "sol_visible").collect()
    assert wins.height == 10
    assert wins[pipeline.WINDOW_VISIBLE].to_list() == [True, False] * 5
    assert wins.filter(pl.col(pipeline.WINDOW_COMPLETE))[pipeline.WINDOW_HOURS].unique().to_list() == [12.0]
    assert wins[pipeline.WINDOW_COMPLETE].to_list()[0] is False and wins[pipeline.WINDOW_COMPLETE].to_list()[-1] is False
    assert wins.row(0, named=True)[pipeline.WINDOW_START] == START


def test_tierra_siempre_visible_es_un_solo_tramo_incompleto():
    wins = pipeline.windows(pipeline.with_visibility(build(48, square)), "tierra_visible").collect()
    assert wins.height == 1 and wins[pipeline.WINDOW_COMPLETE].to_list() == [False]


def test_puestas_y_salidas_alternan():
    events = pipeline.transitions(pipeline.with_visibility(build(24 * 3, square)), "sol_visible").collect()
    assert events[pipeline.EVENT_KIND].to_list() == [pipeline.KIND_SET, pipeline.KIND_RISE] * 2 + [pipeline.KIND_SET]
    assert events[c.COL_UTC][0] == START + timedelta(hours=12)


def test_estadisticas_por_dia_lunar():
    period = c.LUNAR_DAY_DAYS
    frame = build(int(24 * period * 3), lambda i: 50 * math.sin(2 * math.pi * i / (24 * period)))
    days = pipeline.lunar_day_stats(frame, 1.0).collect()
    assert days.height == 3
    for row in days.iter_rows(named=True):
        assert row[c.COL_HORAS_SOL] == pytest.approx(period * 24 / 2, abs=2)
        assert row[c.COL_SOL_EL_MAX] == pytest.approx(50, abs=0.1)
        assert row[c.COL_SOL_EL_MEDIA] == pytest.approx(50 * 2 / math.pi, rel=0.02)


def test_extremos_diarios():
    daily = pipeline.daily_extremes(build(24 * 2, lambda i: float(i % 24) - 11.5)).collect()
    assert daily.height == 2 and daily["sol_el_max"].to_list() == [11.5, 11.5] and daily["sol_el_min"].to_list() == [-11.5] * 2


def test_porcentajes_por_mes_natural():
    frame = build(24 * 40, lambda i: 1.0 if i < 24 * 31 else -1.0)           # dic con Sol, ene sin Sol
    months = pipeline.monthly_percentages(frame).collect()
    assert months[c.COL_PCT_SOL].to_list()[:2] == [100.0, 0.0]
    assert months["muestras"].to_list()[0] == 24 * 31


def test_libracion_no_se_rompe_en_el_salto_de_360_a_0():
    frame = build(24 * 30, square, earth_az=lambda i: 358 + 10 * math.sin(2 * math.pi * i / 240),
                  earth_el=lambda i: 5 * math.sin(2 * math.pi * i / 240))
    lib = pipeline.libration(frame).collect().row(0, named=True)
    assert lib["tierra_az_rango_deg"] == pytest.approx(20, abs=0.2)
    assert lib["tierra_el_rango_deg"] == pytest.approx(10, abs=0.1)

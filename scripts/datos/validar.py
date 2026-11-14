#!/usr/bin/env python3
"""Validación básica contra docs/plan/estandar-csv.md. Uso: python3 validar.py [data_dir]  (por defecto ../../data)"""
from pathlib import Path as _P
DATA_DIR = str(_P(__file__).resolve().parents[2] / "data")
import csv, sys, re
from datetime import datetime, timedelta
from pathlib import Path
d = Path(sys.argv[1] if len(sys.argv) > 1 else DATA_DIR); err = []
sites = list(csv.DictReader(open(d/"sites.csv", encoding="utf-8")))
ids = [s["site_id"] for s in sites]
assert len(ids) == len(set(ids))
for s in sites:
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,39}", s["site_id"]) and s["fuente_url"].startswith("https://") and " " not in s["fuente_url"]
    assert -90 <= float(s["lat_deg"]) <= 90 and -180 <= float(s["lon_este_deg"]) <= 180
    r = list(csv.DictReader(open(d/f"efemerides_{s['site_id']}.csv", encoding="utf-8")))
    t = [datetime.strptime(x["utc"], "%Y-%m-%dT%H:%MZ") for x in r]
    assert len(r) == 745 and all(b-a == timedelta(hours=1) for a, b in zip(t, t[1:])), s["site_id"]
    for k in ("sol_el_deg", "tierra_el_deg"):
        v = [float(x[k]) for x in r]
        m = max(abs(b-a) for a, b in zip(v, v[1:]))
        if m > 1.0: err.append((s["site_id"], k, m))
    for k in ("sol_az_deg", "tierra_az_deg"):
        assert all(0 <= float(x[k]) < 360 for x in r)
for m in csv.DictReader(open(d/"metricas.csv", encoding="utf-8")):
    assert m["site_id"] in ids and abs(float(m["pct_sol"]) - float(m["horas_sol"])/744*100) <= .5
print("ERRORES:", err or "ninguno")

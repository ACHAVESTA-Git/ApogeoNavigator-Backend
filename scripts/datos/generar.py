#!/usr/bin/env python3
"""Descarga efemérides de JPL Horizons y genera sites.csv, efemerides_*.csv y metricas.csv.
Uso: python3 generar.py [data_dir]  (por defecto ../../data)
Rango: 2026-12-01 a 2027-06-01, paso 1 h; metricas.csv: una fila por sitio y mes."""
from pathlib import Path as _P
DATA_DIR = str(_P(__file__).resolve().parents[2] / "data")
import csv, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sitios import SITES

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
INICIO, FIN = datetime(2026,12,1,tzinfo=timezone.utc), datetime(2027,6,1,tzinfo=timezone.utc)
FMT = "%Y-%m-%dT%H:%MZ"
STEP_HOURS = 1                # paso de las efemérides (estándar: de 10 min a 24 h)
METRICS_MAX_DAYS = 35         # máximo de días de una fila de metricas.csv (estándar §4)

def horizons(cuerpo, lat, lon, inicio=INICIO, fin=FIN):
    p = {"format":"text","COMMAND":f"'{cuerpo}'","OBJ_DATA":"'NO'","MAKE_EPHEM":"'YES'","EPHEM_TYPE":"'OBSERVER'",
         "CENTER":"'coord@301'","COORD_TYPE":"'GEODETIC'","SITE_COORD":f"'{lon},{lat},0'",
         "START_TIME":f"'{inicio:%Y-%m-%d %H:%M}'","STOP_TIME":f"'{fin:%Y-%m-%d %H:%M}'","STEP_SIZE":f"'{STEP_HOURS} h'",
         "QUANTITIES":"'4'","CSV_FORMAT":"'YES'","CAL_FORMAT":"'CAL'"}
    url = API + "?" + urllib.parse.urlencode(p, quote_via=urllib.parse.quote)
    for i in range(3):
        try:
            txt = urllib.request.urlopen(url, timeout=60).read().decode()
            break
        except Exception:
            time.sleep(3)
    else:
        raise SystemExit("Horizons no responde")
    filas = txt.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines()
    out = {}
    for f in filas:
        c = [x.strip() for x in f.split(",")]
        t = datetime.strptime(c[0], "%Y-%b-%d %H:%M").replace(tzinfo=timezone.utc)
        out[t] = (float(c[3]), float(c[4]))
    return out, txt

def meses():
    """Meses completos [día 1, día 1 del mes siguiente) dentro del rango (cada uno de <= 35 días)."""
    ini = INICIO
    while ini < FIN:
        fin = datetime(ini.year + ini.month // 12, ini.month % 12 + 1, 1, tzinfo=timezone.utc)
        assert (min(fin, FIN) - ini).days <= METRICS_MAX_DAYS, "una fila de métricas supera el máximo del estándar"
        yield ini, min(fin, FIN)
        ini = fin


def main(data):
    data = Path(data); data.mkdir(parents=True, exist_ok=True)
    with open(data/"sites.csv","w",encoding="utf-8",newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["site_id","nombre","categoria","mision","agencia","lat_deg","lon_este_deg","alt_km","fuente_url","notas"])
        for s in SITES:
            w.writerow([s[0],s[1],s[2],s[3],s[4],s[5],s[6],"",s[7].replace(" ","%20"),s[8]])
    met = []
    horas_rango = int((FIN-INICIO).total_seconds()/3600/STEP_HOURS)
    for s in SITES:
        sid, lat, lon = s[0], s[5], s[6]
        sol, _ = horizons("10", lat, lon); tie, _ = horizons("399", lat, lon)
        ts = sorted(sol)
        assert ts == sorted(tie) and len(ts) == horas_rango+1, (sid, len(ts))
        with open(data/f"efemerides_{sid}.csv","w",encoding="utf-8",newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["utc","sol_az_deg","sol_el_deg","tierra_az_deg","tierra_el_deg"])
            for t in ts:
                w.writerow([t.strftime(FMT), f"{sol[t][0]%360:.6f}".replace("360.000000","0.000000"), f"{sol[t][1]:.6f}",
                            f"{tie[t][0]%360:.6f}".replace("360.000000","0.000000"), f"{tie[t][1]:.6f}"])
        for ini, fin in meses():
            u = [t for t in ts if ini <= t < fin]            # intervalo [ini, fin): una muestra = 1 h
            es = [sol[t][1] for t in u]; et = [tie[t][1] for t in u]
            up = [e for e in es if e > 0]
            hs, ht, n = len(up), sum(1 for e in et if e > 0), len(u)
            met.append([sid, ini.strftime(FMT), fin.strftime(FMT), hs, round(hs/n*100,2), round(max(es),4),
                        round(sum(up)/hs,4) if hs else "", ht, round(ht/n*100,2)])
        print(sid, flush=True); time.sleep(0.5)
    with open(data/"metricas.csv","w",encoding="utf-8",newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["site_id","inicio_utc","fin_utc","horas_sol","pct_sol","sol_el_max_deg","sol_el_media_deg","horas_tierra_visible","pct_tierra"])
        w.writerows(met)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DATA_DIR)

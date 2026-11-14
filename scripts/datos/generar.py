#!/usr/bin/env python3
"""Descarga efemérides de JPL Horizons y genera sites.csv, efemerides_*.csv y metricas.csv.
Uso: python3 generar.py [data_dir]  (por defecto ../../data)"""
from pathlib import Path as _P
DATA_DIR = str(_P(__file__).resolve().parents[2] / "data")
import csv, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sitios import SITES

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
INICIO, FIN = datetime(2026,12,1,tzinfo=timezone.utc), datetime(2027,1,1,tzinfo=timezone.utc)
FMT = "%Y-%m-%dT%H:%MZ"

def horizons(cuerpo, lat, lon):
    p = {"format":"text","COMMAND":f"'{cuerpo}'","OBJ_DATA":"'NO'","MAKE_EPHEM":"'YES'","EPHEM_TYPE":"'OBSERVER'",
         "CENTER":"'coord@301'","COORD_TYPE":"'GEODETIC'","SITE_COORD":f"'{lon},{lat},0'",
         "START_TIME":"'2026-12-01 00:00'","STOP_TIME":"'2027-01-01 00:00'","STEP_SIZE":"'1 h'",
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

def main(data):
    data = Path(data); data.mkdir(parents=True, exist_ok=True)
    with open(data/"sites.csv","w",encoding="utf-8",newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["site_id","nombre","categoria","mision","agencia","lat_deg","lon_este_deg","alt_km","fuente_url","notas"])
        for s in SITES:
            w.writerow([s[0],s[1],s[2],s[3],s[4],s[5],s[6],"",s[7].replace(" ","%20"),s[8]])
    met = []
    horas_rango = int((FIN-INICIO).total_seconds()/3600)
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
        # métricas sobre el intervalo [inicio, fin): 744 muestras de 1 h
        u = ts[:horas_rango]
        es = [sol[t][1] for t in u]; et = [tie[t][1] for t in u]
        up = [e for e in es if e > 0]
        hs, ht = len(up), sum(1 for e in et if e > 0)
        met.append([sid, INICIO.strftime(FMT), FIN.strftime(FMT), hs, round(hs/horas_rango*100,2), round(max(es),4),
                    round(sum(up)/hs,4) if hs else "", ht, round(ht/horas_rango*100,2)])
        print(sid, hs, ht, flush=True); time.sleep(0.5)
    with open(data/"metricas.csv","w",encoding="utf-8",newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["site_id","inicio_utc","fin_utc","horas_sol","pct_sol","sol_el_max_deg","sol_el_media_deg","horas_tierra_visible","pct_tierra"])
        w.writerows(met)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DATA_DIR)

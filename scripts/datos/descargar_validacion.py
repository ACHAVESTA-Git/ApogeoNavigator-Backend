#!/usr/bin/env python3
"""Descarga de JPL Horizons los 12 meses siguientes a los datos (2027-06-01 a 2028-06-01, paso 1 h)
como VERDAD DE TERRENO para validar la proyección. Escribe un único Parquet con Polars.

Uso: python3 descargar_validacion.py [archivo.parquet]   (por defecto validacion/horizons_verdad.parquet)
Los datos de validación NO se sirven por la API: solo sirven para medir el error de la proyección.
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from generar import STEP_HOURS, horizons
from sitios import SITES

VAL_INICIO = datetime(2027, 6, 1, tzinfo=timezone.utc)
VAL_FIN = datetime(2028, 6, 1, tzinfo=timezone.utc)
DECIMALS = 6                      # igual que los CSV
DEFAULT_OUT = Path(__file__).resolve().parent / "validacion" / "horizons_verdad.parquet"
PAUSE_S = 0.5                     # cortesía con JPL


def descargar() -> pl.DataFrame:
    frames = []
    for site_id, lat, lon in ((s[0], s[5], s[6]) for s in SITES):
        sol, _ = horizons("10", lat, lon, VAL_INICIO, VAL_FIN)
        tierra, _ = horizons("399", lat, lon, VAL_INICIO, VAL_FIN)
        times = sorted(sol)
        assert times == sorted(tierra), site_id
        frames.append(pl.DataFrame({
            "site_id": [site_id] * len(times),
            "utc": times,
            "sol_az_deg": [round(sol[t][0] % 360, DECIMALS) for t in times],
            "sol_el_deg": [round(sol[t][1], DECIMALS) for t in times],
            "tierra_az_deg": [round(tierra[t][0] % 360, DECIMALS) for t in times],
            "tierra_el_deg": [round(tierra[t][1], DECIMALS) for t in times],
        }))
        print(site_id, len(times), flush=True)
        time.sleep(PAUSE_S)
    return pl.concat(frames).with_columns(pl.col("utc").dt.cast_time_unit("ms"))


def main(out: Path) -> None:
    df = descargar()
    expected = int((VAL_FIN - VAL_INICIO).total_seconds() / 3600 / STEP_HOURS) + 1
    assert df.group_by("site_id").len()["len"].to_list() == [expected] * len(SITES), "filas inesperadas"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out, compression="zstd", compression_level=15, statistics=True)
    print(f"{out} · {out.stat().st_size / 1024:.0f} KiB · {df.height} filas")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT)

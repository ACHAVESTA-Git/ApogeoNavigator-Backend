#!/usr/bin/env python3
"""Mide el error REAL de la proyección armónica (2027-06 a 2028-07) contra JPL Horizons.

Entradas: data/proyecciones/proyeccion_<site>.csv, scripts/datos/validacion/horizons_verdad.parquet
(2027-06-01 a 2028-06-01, 10 sitios) y, para junio y julio de 2028, una verdad de 3 sitios que este
script descarga de Horizons UNA sola vez (6 peticiones) y guarda en horizons_verdad_2028_junjul.parquet.
Salidas: informe en stdout (Markdown) y el bloque «error» de data/proyecciones/modelo.json.

Uso: python3 scripts/datos/validar_proyeccion_2028.py [--solo-medir]   (PYTHONPATH = raíz del repo)
La proyección se publica con su error: esto es lo que lo mide, no una efeméride.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))   # generar.py y sitios.py viven aquí

from app.core import config as c                       # noqa: E402
from app.services.lectura import traffic_light         # noqa: E402  (la regla real del semáforo)

# ---------------------------------------------------------------- constantes
DATA = RAIZ / "data"
PROYECCIONES = DATA / "proyecciones"
MODELO_JSON = PROYECCIONES / "modelo.json"
VERDAD_12M = RAIZ / "scripts/datos/validacion/horizons_verdad.parquet"
VERDAD_JUNJUL = RAIZ / "scripts/datos/validacion/horizons_verdad_2028_junjul.parquet"
SITES_CSV = DATA / "sites.csv"
FMT = "%Y-%m-%dT%H:%MZ"
DEC = 4                                                 # decimales del JSON
VARS = ("sol_el_deg", "tierra_el_deg", "sol_az_deg", "tierra_az_deg")
VARS_AZ = ("sol_az_deg", "tierra_az_deg")
CUERPOS = (("sol", "10"), ("tierra", "399"))
SITIOS_3 = ("shackleton", "apollo11", "change4")      # única verdad de jun-jul 2028
SITIOS_POLARES = ("shackleton", "im1_odysseus")
INICIO_HORIZONTE = datetime(2027, 6, 1, tzinfo=timezone.utc)   # mes de horizonte 1
MESES_12 = 12                                           # meses con los 10 sitios
MESES_MAX = 14                                          # último mes alcanzado
HORIZONTES_EQUIPO = (1, 3, 6, 12, 14)
UMBRALES_ELEV = (1.0, 2.0, 5.0)
VENTANA_JUNJUL = (datetime(2028, 6, 1, tzinfo=timezone.utc), datetime(2028, 7, 31, 23, tzinfo=timezone.utc))
INSTANTES_CONTROL = ("2027-12-01T00:00Z", "2028-01-15T12:00Z", "2028-07-15T12:00Z")
CABECERAS_OBLIGATORIAS = ("Moon (301)", "AIRLESS")      # comprobación de la respuesta de Horizons
N_PETICIONES_MAX = 8


# ---------------------------------------------------------------- utilitarios
def dif_circular(a, b):
    """Diferencia de azimut a - b envuelta a [-180, 180)."""
    return (np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0


def vec(az, el):
    """Vector unitario (az desde el norte hacia el este, el sobre el horizonte)."""
    a, e = np.radians(az), np.radians(el)
    return np.stack([np.cos(e) * np.sin(a), np.cos(e) * np.cos(a), np.sin(e)], axis=-1)


def arco_deg(az1, el1, az2, el2):
    """Ángulo (°) entre dos direcciones; atan2 evita el error de acos cerca de 0."""
    v1, v2 = vec(az1, el1), vec(az2, el2)
    return np.degrees(np.arctan2(np.linalg.norm(np.cross(v1, v2), axis=-1), np.sum(v1 * v2, axis=-1)))


def rmse(x):
    x = np.asarray(x, dtype=float)
    return float(np.sqrt(np.mean(x ** 2)))


def mae(x):
    return float(np.mean(np.abs(np.asarray(x, dtype=float))))


def indice_mes(utc: datetime) -> int:
    """Mes de horizonte (1 = junio de 2027) de un instante."""
    return (utc.year - INICIO_HORIZONTE.year) * 12 + utc.month - INICIO_HORIZONTE.month + 1


def nombre_mes(m: int) -> str:
    total = INICIO_HORIZONTE.month - 1 + m - 1
    return f"{INICIO_HORIZONTE.year + total // 12}-{total % 12 + 1:02d}"


def media(valores):
    return float(np.mean(valores)) if len(valores) else float("nan")


def r4(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), DEC)


def fmt_utc(t) -> str:
    return t.strftime(FMT)


# ---------------------------------------------------------------- datos
def descargar_junjul() -> pl.DataFrame:
    """Horizons jun-jul 2028, Sol y Tierra, solo SITIOS_3: 2 peticiones por sitio (6 en total)."""
    from generar import horizons                        # misma petición que las efemérides del proyecto
    sitios = {r["site_id"]: r for r in pl.read_csv(SITES_CSV).iter_rows(named=True)}
    marcos, peticiones = [], 0
    for sid in SITIOS_3:
        lat, lon = sitios[sid]["lat_deg"], sitios[sid]["lon_este_deg"]
        datos = {}
        for nombre, cuerpo in CUERPOS:
            peticiones += 1
            assert peticiones <= N_PETICIONES_MAX, "demasiadas peticiones a Horizons"
            datos[nombre], txt = horizons(cuerpo, lat, lon, *VENTANA_JUNJUL)
            assert all(h in txt for h in CABECERAS_OBLIGATORIAS), f"cabecera inesperada en {sid}/{nombre}"
        ts = sorted(datos["sol"])
        assert ts == sorted(datos["tierra"]), sid
        marcos.append(pl.DataFrame({
            "site_id": [sid] * len(ts), "utc": ts,
            "sol_az_deg": [round(datos["sol"][t][0] % 360, 6) for t in ts],
            "sol_el_deg": [round(datos["sol"][t][1], 6) for t in ts],
            "tierra_az_deg": [round(datos["tierra"][t][0] % 360, 6) for t in ts],
            "tierra_el_deg": [round(datos["tierra"][t][1], 6) for t in ts]}))
        print(f"descargado {sid}: {len(ts)} filas", file=sys.stderr)
    df = pl.concat(marcos).with_columns(pl.col("utc").dt.cast_time_unit("ms"))
    df.write_parquet(VERDAD_JUNJUL, compression="zstd", statistics=True)
    return df


def cargar_verdad() -> pl.DataFrame:
    """Verdad completa: 12 meses (10 sitios) + jun-jul 2028 (3 sitios), sin instantes repetidos."""
    base = pl.read_parquet(VERDAD_12M).filter(pl.col("utc") < VENTANA_JUNJUL[0])   # la hora 2028-06-01 00:00 es de jun-jul
    jj = pl.read_parquet(VERDAD_JUNJUL) if VERDAD_JUNJUL.exists() else descargar_junjul()
    return pl.concat([base, jj.select(base.columns)]).unique(["site_id", "utc"]).sort("site_id", "utc")


def cargar_union(verdad: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Por sitio: proyección y verdad en las horas comunes, con columnas p_* (proyección) y t_* (verdad)."""
    res = {}
    for sid in verdad["site_id"].unique().sort():
        pr = (pl.read_csv(PROYECCIONES / f"proyeccion_{sid}.csv")
              .with_columns(pl.col("utc").str.strptime(pl.Datetime("ms", "UTC"), "%Y-%m-%dT%H:%MZ"))
              .rename({v: f"p_{v}" for v in VARS}))
        t = verdad.filter(pl.col("site_id") == sid).drop("site_id").rename({v: f"t_{v}" for v in VARS})
        df = pr.join(t, on="utc", how="inner").sort("utc")
        df = df.with_columns(pl.col("utc").map_elements(indice_mes, return_dtype=pl.Int64).alias("mes"))
        res[sid] = df
    return res


def errores(df: pl.DataFrame) -> dict[str, np.ndarray]:
    """Error proyección - Horizons por variable (azimut circular) y arco angular del Sol y de la Tierra."""
    e = {v: (dif_circular(df[f"p_{v}"], df[f"t_{v}"]) if v in VARS_AZ
             else (df[f"p_{v}"] - df[f"t_{v}"]).to_numpy()) for v in VARS}
    for cuerpo, _ in CUERPOS:
        e[f"arco_{cuerpo}"] = arco_deg(df[f"p_{cuerpo}_az_deg"].to_numpy(), df[f"p_{cuerpo}_el_deg"].to_numpy(),
                                       df[f"t_{cuerpo}_az_deg"].to_numpy(), df[f"t_{cuerpo}_el_deg"].to_numpy())
    return e


# ---------------------------------------------------------------- medidas 1 y 2
def por_horizonte(union):
    """Por mes de horizonte: RMSE/MAE por variable y arco, promediados sobre los sitios con verdad ese mes."""
    filas = []
    for m in range(1, MESES_MAX + 1):
        por_sitio = []
        for sid, df in union.items():
            sub = df.filter(pl.col("mes") == m)
            if sub.height:
                e = errores(sub)
                por_sitio.append({k: (rmse(x), mae(x)) for k, x in e.items()})
        fila = {"meses": m, "n_sitios": len(por_sitio)}
        for k in por_sitio[0]:
            fila[f"rmse_{k}"] = media([s[k][0] for s in por_sitio])
            fila[f"mae_{k}"] = media([s[k][1] for s in por_sitio])
        filas.append(fila)
    return filas


def rmse_global(union):
    """RMSE de los 12 meses con 10 sitios, por variable, promediado sobre sitios."""
    out = {v: [] for v in VARS}
    for df in union.values():
        sub = df.filter(pl.col("mes") <= MESES_12)
        e = errores(sub)
        for v in VARS:
            out[v].append(rmse(e[v]))
    return {v: media(x) for v, x in out.items()}, {v: dict(zip(union, x)) for v, x in out.items()}


# ---------------------------------------------------------------- medida 3
def control_puntual(union):
    filas = []
    for sid in SITIOS_3:
        for iso in INSTANTES_CONTROL:
            t = datetime.strptime(iso, FMT).replace(tzinfo=timezone.utc)
            sub = union[sid].filter(pl.col("utc") == t)
            assert sub.height == 1, (sid, iso)
            e = errores(sub)
            for v in VARS:
                filas.append({"site_id": sid, "utc": iso, "variable": v, "error_deg": float(e[v][0])})
    return filas


# ---------------------------------------------------------------- medida 4
def pct_mensual(union):
    """pct_sol y pct_tierra por (sitio, mes) con la proyección y con Horizons, y los semáforos."""
    filas = []
    for sid, df in union.items():
        for m in sorted(df["mes"].unique()):
            sub = df.filter(pl.col("mes") == m)
            f = {"site_id": sid, "mes": m, "n_horas": sub.height}
            for origen, pref in (("proy", "p_"), ("hor", "t_")):
                f[f"pct_sol_{origen}"] = float((sub[f"{pref}sol_el_deg"] > 0).mean() * 100)
                f[f"pct_tierra_{origen}"] = float((sub[f"{pref}tierra_el_deg"] > 0).mean() * 100)
                f[f"sem_{origen}"] = traffic_light(f[f"pct_sol_{origen}"], f[f"pct_tierra_{origen}"])
            filas.append(f)
    return filas


def aciertos(filas):
    """Aciertos de semáforo global, de energía y de comunicación: por mes de horizonte y total."""
    claves = {"global": c.KEY_GLOBAL, "energia": c.KEY_ENERGIA, "comunicacion": c.KEY_COMUNICACION}
    por_mes, total = {}, {k: [0, 0] for k in claves}
    for f in filas:
        d = por_mes.setdefault(f["mes"], {k: [0, 0] for k in claves})
        for k, col in claves.items():
            ok = f["sem_proy"][col] == f["sem_hor"][col]
            d[k][0] += ok; d[k][1] += 1
            total[k][0] += ok; total[k][1] += 1
    return por_mes, total


def control_semaforo(filas):
    """Aciertos del semáforo global en la rejilla de control (todos los pares sitio-mes con verdad)."""
    ok = sum(f["sem_proy"][c.KEY_GLOBAL] == f["sem_hor"][c.KEY_GLOBAL] for f in filas)
    return {"aciertos": int(ok), "total": len(filas)}


# ---------------------------------------------------------------- medida 5
def cruces_ascendentes(t_h: np.ndarray, el: np.ndarray) -> np.ndarray:
    """Instantes (horas) de los cruces de elevación de negativo a positivo, por interpolación lineal."""
    i = np.where((el[:-1] <= 0) & (el[1:] > 0))[0]
    return t_h[i] + (0 - el[i]) / (el[i + 1] - el[i]) * (t_h[i + 1] - t_h[i])


def desfase_sinodico(union, sid, m_ini, m_fin):
    """Desfase medio (h, proyección - Horizons) de las salidas del Sol entre los meses m_ini y m_fin."""
    df = union[sid].filter(pl.col("mes").is_between(m_ini, m_fin))
    if df.is_empty():
        return None
    t = (df["utc"].dt.epoch("ms").to_numpy() / 3.6e6).astype(float)
    cp = cruces_ascendentes(t, df["p_sol_el_deg"].to_numpy())
    ct = cruces_ascendentes(t, df["t_sol_el_deg"].to_numpy())
    if not len(cp) or not len(ct):
        return None
    d = [float(x - ct[np.argmin(np.abs(ct - x))]) for x in cp]
    return {"n_cruces": len(d), "desfase_h": float(np.mean(d))}


def max_polar_mensual(union, sid):
    """Altura máxima mensual del Sol (°) de proyección y de Horizons, mes a mes."""
    df = union[sid]
    g = df.group_by("mes").agg(pl.col("p_sol_el_deg").max().alias("p"), pl.col("t_sol_el_deg").max().alias("t"),
                               pl.len().alias("n")).sort("mes")
    return g.to_dicts()


# ---------------------------------------------------------------- informe
def tabla(cabecera, filas):
    return "\n".join(["| " + " | ".join(cabecera) + " |", "|" + "---|" * len(cabecera)]
                     + ["| " + " | ".join(map(str, f)) + " |" for f in filas])


def informe(union, hor, glob, glob_sitio, control, pct, ac_mes, ac_tot):
    f4 = lambda x: f"{x:.4f}"
    print("## A. Verdad: filas por sitio y rango")
    for sid, df in union.items():
        print(f"- {sid}: {df.height} horas comunes, {df['utc'].min():%Y-%m-%dT%H:%MZ} a {df['utc'].max():%Y-%m-%dT%H:%MZ}")
    print("\n## 1. Error por mes de horizonte (promedio de RMSE por sitio)")
    print(tabla(["mes", "periodo", "n_sit", "RMSE sol_el", "RMSE tierra_el", "RMSE sol_az", "RMSE tierra_az",
                 "MAE sol_el", "MAE tierra_el", "MAE sol_az", "MAE tierra_az", "arco sol (RMSE)", "arco tierra (RMSE)"],
                [[h["meses"], nombre_mes(h["meses"]), h["n_sitios"]] + [f4(h[k]) for k in (
                    "rmse_sol_el_deg", "rmse_tierra_el_deg", "rmse_sol_az_deg", "rmse_tierra_az_deg",
                    "mae_sol_el_deg", "mae_tierra_el_deg", "mae_sol_az_deg", "mae_tierra_az_deg",
                    "rmse_arco_sol", "rmse_arco_tierra")] for h in hor]))
    print("\nHorizontes del equipo:", ", ".join(str(m) for m in HORIZONTES_EQUIPO))
    for u in UMBRALES_ELEV:
        for v in ("sol_el_deg", "tierra_el_deg"):
            p = next((h["meses"] for h in hor if h[f"rmse_{v}"] > u), None)
            print(f"- RMSE {v} supera {u:g} grados por primera vez en el mes {p}")
    print("\n## 2. RMSE global (12 meses, 10 sitios; promedio de RMSE por sitio)")
    for v in VARS:
        print(f"- {v}: {glob[v]:.4f}  (min sitio {min(glob_sitio[v].values()):.4f}, max sitio {max(glob_sitio[v].values()):.4f})")
    print("\n## 3. Control puntual (proyección - Horizons, grados)")
    print(tabla(["sitio", "utc"] + list(VARS), [[s, i] + [f"{next(r['error_deg'] for r in control if r['site_id'] == s and r['utc'] == i and r['variable'] == v):+.4f}"
                                                         for v in VARS] for s in SITIOS_3 for i in INSTANTES_CONTROL]))
    print("\n## 4. Semáforo: aciertos por mes de horizonte")
    print(tabla(["mes", "periodo", "global", "energía", "comunicación"],
                [[m, nombre_mes(m)] + [f"{d[k][0]}/{d[k][1]}" for k in ("global", "energia", "comunicacion")] for m, d in sorted(ac_mes.items())]))
    print("Total:", ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in ac_tot.items()))
    print("\nFallos (global, energía o comunicación):")
    fallos = [f for f in pct if any(f["sem_proy"][k] != f["sem_hor"][k] for k in (c.KEY_GLOBAL, c.KEY_ENERGIA, c.KEY_COMUNICACION))]
    print(tabla(["sitio", "mes", "pct_sol proy/hor", "pct_tierra proy/hor", "energía proy/hor", "comunic. proy/hor", "global proy/hor"],
                [[f["site_id"], nombre_mes(f["mes"]), f"{f['pct_sol_proy']:.2f}/{f['pct_sol_hor']:.2f}",
                  f"{f['pct_tierra_proy']:.2f}/{f['pct_tierra_hor']:.2f}"]
                 + [f"{f['sem_proy'][k]}/{f['sem_hor'][k]}" for k in (c.KEY_ENERGIA, c.KEY_COMUNICACION, c.KEY_GLOBAL)] for f in fallos]) if fallos else "(ninguno)")
    print("\nShackleton, pct_sol por mes (umbral verde 45):")
    print(tabla(["mes", "proy", "Horizons", "dif", "sem. energía proy/hor"],
                [[nombre_mes(f["mes"]), f"{f['pct_sol_proy']:.2f}", f"{f['pct_sol_hor']:.2f}",
                  f"{f['pct_sol_proy'] - f['pct_sol_hor']:+.2f}", f"{f['sem_proy'][c.KEY_ENERGIA]}/{f['sem_hor'][c.KEY_ENERGIA]}"]
                 for f in pct if f["site_id"] == "shackleton"]))
    errp = [abs(f["pct_sol_proy"] - f["pct_sol_hor"]) for f in pct]
    errt = [abs(f["pct_tierra_proy"] - f["pct_tierra_hor"]) for f in pct]
    print(f"\nError absoluto medio de pct_sol: {np.mean(errp):.2f} puntos (máx {max(errp):.2f}); pct_tierra: {np.mean(errt):.2f} (máx {max(errt):.2f})")
    print("\n## 5a. Fase del Sol (salidas del Sol, proyección - Horizons, horas)")
    rangos = [(1, 1), (6, 6), (12, 12), (13, 13), (14, 14)]
    filas = []
    for sid in union:
        filas.append([sid] + [("%+.3f (%d)" % (r["desfase_h"], r["n_cruces"]) if (r := desfase_sinodico(union, sid, a, b)) else "n/d") for a, b in rangos])
    print(tabla(["sitio"] + [f"mes {a}" for a, _ in rangos], filas))
    print("\n## 5b. Altura máxima mensual del Sol en sitios polares (proyección vs Horizons, °)")
    for sid in SITIOS_POLARES:
        g = max_polar_mensual(union, sid)
        x = np.array([r["mes"] for r in g], float); d = np.array([r["p"] - r["t"] for r in g])
        pend = np.polyfit(x, d, 1)[0]
        print(f"\n{sid}: pendiente de la diferencia {pend:+.4f} grados/mes, corr(mes, dif)={np.corrcoef(x, d)[0, 1]:+.2f}")
        print(tabla(["mes", "periodo", "max proy", "max Horizons", "dif"],
                    [[r["mes"], nombre_mes(r["mes"]), f"{r['p']:.4f}", f"{r['t']:.4f}", f"{r['p'] - r['t']:+.4f}"] for r in g]))


# ---------------------------------------------------------------- salida a modelo.json
def escribir_modelo(hor, glob, control, ctrl_sem):
    modelo = json.loads(MODELO_JSON.read_text(encoding="utf-8"))
    modelo["error"] = {
        "rmse_deg": {v: r4(glob[v]) for v in VARS},
        "por_horizonte": [{"meses": h["meses"], "rmse_sol_el_deg": r4(h["rmse_sol_el_deg"]),
                           "rmse_tierra_el_deg": r4(h["rmse_tierra_el_deg"])} for h in hor],
        "control_horizons": [{**r, "error_deg": r4(r["error_deg"])} for r in control],
        "aciertos_semaforo_control": ctrl_sem,
    }
    MODELO_JSON.write_text(json.dumps(modelo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    verdad = cargar_verdad()
    union = cargar_union(verdad)
    hor = por_horizonte(union)
    glob, glob_sitio = rmse_global(union)
    control = control_puntual(union)
    pct = pct_mensual(union)
    ac_mes, ac_tot = aciertos(pct)
    informe(union, hor, glob, glob_sitio, control, pct, ac_mes, ac_tot)
    escribir_modelo(hor, glob, control, control_semaforo(pct))
    print(f"\nmodelo.json actualizado: {MODELO_JSON}")


if __name__ == "__main__":
    main()

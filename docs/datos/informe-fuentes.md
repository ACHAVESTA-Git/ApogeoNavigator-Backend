# NASA data sources report · 14 Nov 2026

> English version first; the original Spanish report follows unchanged after the line "Versión original en español". Both describe the same data.

Real data for the range 2026-12-01T00:00Z → 2027-06-01T00:00Z (6 months, 4369 rows per site, 1-hour step). `metricas.csv` has one row per site and month (60 rows). The outcome of the data review is at the end of this report.

## 1. What each source provides and what we took

| Source | What it offers | What we took |
|---|---|---|
| **JPL Horizons API** (`ssd.jpl.nasa.gov/api/horizons.api`) | Apparent position (azimuth and elevation) of the Sun and the Earth as seen from a point on the Moon (DE441 ephemerides, no refraction) | All the ephemerides and, derived from them, the metrics |
| **NSSDC** (`nssdc.gsfc.nasa.gov/planetary/lunar/lunar_sites.html`) | Coordinates of the Apollo lunar modules (LRO, Wagner et al. 2017) | Apollo 11, 15 and 17 |
| **LROC / LRO (NASA/GSFC/ASU)** | Positions of recent landers measured on LRO images | Shackleton, Blue Ghost 1, Chandrayaan-3, Chang'e 4, Chang'e 6, SLIM |
| **nasa.gov (LRO, IM-1)** | Odysseus coordinates | IM-1 |
| **nasa.gov (Artemis III)** | 13 candidate regions, all within 6° of the south pole. **It does not publish coordinates** | Nothing: without coordinates they are not included |
| Moon Trek / QuickMap / PGDA 69 and 90 | Verification of illumination and elevation | Not consulted: they are viewers or rasters, with no simple queryable service. They remain as a pending qualitative validation (backlog item HU-12) |
| HiRISE, EarthExplorer | Not applicable (Mars and Earth) | — |

## 2. Sites (10)

| site_id | Site | Lat | Lon E | Source |
|---|---|---|---|---|
| apollo11 | Apollo 11 | 0.67416 | 23.47314 | NSSDC |
| apollo15 | Apollo 15 | 26.13239 | 3.6333 | NSSDC |
| apollo17 | Apollo 17 | 20.1911 | 30.7723 | NSSDC |
| shackleton | Shackleton crater (centre) | −89.66 | 129.2 | LROC posts/993 |
| im1_odysseus | IM-1 Odysseus | −80.13 | 1.44 | nasa.gov (LRO) |
| blue_ghost_m1 | Blue Ghost 1 | 18.56 | 61.81 | LROC images/1400 |
| chandrayaan3 | Chandrayaan-3 | −69.3741 | 32.32 | LROC images/1314 |
| change4 | Chang'e 4 | −45.457 | 177.589 | LROC posts/1087 |
| change6 | Chang'e 6 | −41.6385 | −153.9852 | LROC images/1374 |
| slim | SLIM | −13.316 | 25.251 | LROC images/1358 |

**Excluded for lack of a reliable source:** the Artemis III regions (no official coordinates), Haworth and Malapert Massif, IM-2 and Firefly other than Blue Ghost 1. One web page claims "finalised" Artemis III coordinates for Malapert; it does not cite NASA and was discarded.

## 3. How the data were extracted (exact queries)

Scripts in `scripts/datos/` (`sitios.py`, `generar.py`, `validar.py`). For each site and body (Sun `'10'`, Earth `'399'`):

```
https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND='10'&OBJ_DATA='NO'&MAKE_EPHEM='YES'
 &EPHEM_TYPE='OBSERVER'&CENTER='coord@301'&COORD_TYPE='GEODETIC'&SITE_COORD='<lon>,<lat>,0'
 &START_TIME='2026-12-01 00:00'&STOP_TIME='2027-06-01 00:00'&STEP_SIZE='1 h'&QUANTITIES='4'&CSV_FORMAT='YES'
```

`SITE_COORD` order: **lon, lat, alt** (East positive). The two series are joined on `utc`. Each ephemeris file has 4369 rows. Each metrics row covers one calendar month, as the half-open interval [start, end) (≤ 35 days): `horas_sol` and `horas_tierra_visible` count the samples with elevation > 0; `sol_el_media_deg` is the mean over the hours with the Sun up only.

## 4. Results and findings

December 2026 table (the first monthly row of `metricas.csv`; the other months are in the CSV).

| Site | Sun % | Max Sun elev. | Earth % |
|---|---|---|---|
| Apollo 11 | 47.85 | 88.09° | 100 |
| Apollo 15 | 49.19 | 62.67° | 100 |
| Apollo 17 | 47.58 | 68.55° | 100 |
| Shackleton | 100* | 1.74° | 39.11 |
| IM-1 (80.13° S) | 56.05 | 11.07° | 100 |
| Blue Ghost 1 | 47.58 | 70.13° | 100 |
| Chandrayaan-3 | 49.6 | 21.88° | 100 |
| Chang'e 4 | 49.73 | 45.98° | **0** |
| Chang'e 6 | 52.69 | 49.81° | **0** |
| SLIM | 47.98 | 77.92° | 100 |

\* Ideal-horizon artefact (see section 5).

- **Grazing Sun at the pole:** at Shackleton the Sun never rises above 1.74° over the horizon, against about 88° at Apollo 11. Very long shadows and almost horizontal solar energy.
- **Earth never visible:** Chang'e 4 and Chang'e 6 (far side) have 0 h of Earth; a relay satellite (Queqiao) is needed to communicate.
- **Same light, different communications:** Chang'e 4 and Apollo 15 have almost the same Sun percentage (≈49 %), but one sees the Earth all the time and the other never.
- **South pole and Earth:** at Shackleton the Earth is visible only 39 % of the month (it oscillates between −7° and 6°); at IM-1 (80° S) it is always visible, but only about 10° high.
- **Seasonal change at Shackleton (ideal horizon):** the Sun goes from 100 % in December to 92.7 % in January, 53.3 % in February and 0 % from March to May (maximum elevation −0.43° to −1.17°). With an ideal horizon the point is left without Sun; the Sun becomes grazing again with the lunar season. This is the basis of backlog item HU-08 (same site on different dates).
- **Chang'e 4:** the Earth stays at 0 % for all 6 months and the Sun stays between 47 and 52 %.
- **Lunar-day cycle:** equatorial and mid-latitude sites sit around 48–50 % Sun over 31 days; this is half of the 29.5-day lunar day.

## 5. Limitations

- **Ideal horizon:** Horizons does not model terrain. Shackleton shows 100 % Sun, but its floor is in permanent shadow; the real figure corresponds to nearby ridges. At IM-1 and Chandrayaan-3, with a low Sun, local relief changes the real hours a lot. This warning must be shown next to the metric and validated with PGDA 69/90 or QuickMap (HU-12).
- **Positions:** Blue Ghost 1 is the centre of the landing region (±0.01°), not the touchdown point. Shackleton is the crater centre, not a chosen landing site. The longitude of Chang'e 6 was converted from 206.0148° E.
- **Chandrayaan-3:** the LROC-measured position is used (−69.3741; 32.32). ISRO's planned target is −69.367621; 32.348126 (LVM3-M4 brochure); the difference is a few hundred metres.
- **Range:** six months (Dec 2026 to May 2027), not a full year.
- **Earth prediction:** the Horizons header states that the Earth orientation parameters (EOP) are predictions from February 2027; for an observer on the Moon the effect is below 1 s and does not change the data at 6 decimals.
- **Traceability:** the raw Horizons headers were not saved; they are reproducible with `generar.py`.
- Cite "Source: NASA/JPL Horizons" without suggesting that NASA endorses the project.

## 6. Data review
**APPROVED** for the 10 sites with 6 months (4369 rows per site) and 60 monthly metrics rows. The ephemerides match an independent Horizons download with a maximum difference of 0 over all 4369 rows, and the 60 rows of `metricas.csv` come out identical when recomputed. Months have 672, 720 or 744 h: compare by percentage, not by hours.

---

## Versión original en español

# Informe de fuentes de datos NASA · T5 · 14/11/2026

Datos reales para el rango 2026-12-01T00:00Z → 2027-06-01T00:00Z (6 meses, 4369 filas por sitio, paso de 1 h). `metricas.csv` tiene una fila por sitio y mes (60 filas). Resultado de la revisión de datos: ver el final de este informe.

## 1. Qué aporta cada fuente y qué tomamos

| Fuente | Qué ofrece | Qué tomamos |
|---|---|---|
| **JPL Horizons API** (`ssd.jpl.nasa.gov/api/horizons.api`) | Posición aparente (azimut y elevación) del Sol y la Tierra vistos desde un punto de la Luna (efemérides DE441, sin refracción) | Todas las efemérides y, derivadas, las métricas |
| **NSSDC** (`nssdc.gsfc.nasa.gov/planetary/lunar/lunar_sites.html`) | Coordenadas de los módulos lunares de Apolo (LRO, Wagner et al. 2017) | Apolo 11, 15 y 17 |
| **LROC / LRO (NASA/GSFC/ASU)** | Posiciones medidas sobre imágenes de LRO de módulos de aterrizaje recientes | Shackleton, Blue Ghost 1, Chandrayaan-3, Chang'e 4, Chang'e 6, SLIM |
| **nasa.gov (LRO, IM-1)** | Coordenadas de Odysseus | IM-1 |
| **nasa.gov (Artemis III)** | 13 regiones candidatas, todas a menos de 6° del polo sur. **No publica coordenadas** | Nada: sin coordenadas no se incluyen |
| Moon Trek / QuickMap / PGDA 69 y 90 | Verificación de iluminación y elevación | No se consultaron: son visores o ráster, no hay servicio simple consultable. Quedan como validación cualitativa pendiente (HU-12) |
| HiRISE, EarthExplorer | No aplican (Marte y Tierra) | — |

## 2. Sitios (10)

| site_id | Sitio | Lat | Lon E | Fuente |
|---|---|---|---|---|
| apollo11 | Apolo 11 | 0.67416 | 23.47314 | NSSDC |
| apollo15 | Apolo 15 | 26.13239 | 3.6333 | NSSDC |
| apollo17 | Apolo 17 | 20.1911 | 30.7723 | NSSDC |
| shackleton | Cráter Shackleton (centro) | −89.66 | 129.2 | LROC posts/993 |
| im1_odysseus | IM-1 Odysseus | −80.13 | 1.44 | nasa.gov (LRO) |
| blue_ghost_m1 | Blue Ghost 1 | 18.56 | 61.81 | LROC images/1400 |
| chandrayaan3 | Chandrayaan-3 | −69.3741 | 32.32 | LROC images/1314 |
| change4 | Chang'e 4 | −45.457 | 177.589 | LROC posts/1087 |
| change6 | Chang'e 6 | −41.6385 | −153.9852 | LROC images/1374 |
| slim | SLIM | −13.316 | 25.251 | LROC images/1358 |

**Excluidos por falta de fuente fiable:** regiones de Artemis III (sin coordenadas oficiales), Haworth y Malapert Massif, IM-2 y Firefly fuera de Blue Ghost 1. Hay una página que afirma coordenadas «finalizadas» de Artemis III para Malapert; no cita a NASA y se descartó.

## 3. Cómo se extrajo (consultas exactas)

Scripts en `scripts/datos/` (`sitios.py`, `generar.py`, `validar.py`). Para cada sitio y cuerpo (Sol `'10'`, Tierra `'399'`):

```
https://ssd.jpl.nasa.gov/api/horizons.api?format=text&COMMAND='10'&OBJ_DATA='NO'&MAKE_EPHEM='YES'
 &EPHEM_TYPE='OBSERVER'&CENTER='coord@301'&COORD_TYPE='GEODETIC'&SITE_COORD='<lon>,<lat>,0'
 &START_TIME='2026-12-01 00:00'&STOP_TIME='2027-06-01 00:00'&STEP_SIZE='1 h'&QUANTITIES='4'&CSV_FORMAT='YES'
```

Orden de `SITE_COORD`: **lon, lat, alt** (Este +). Las dos series se unen por `utc`. Cada efeméride tiene 4369 filas. Cada fila de métricas cubre un mes natural, intervalo [inicio, fin) (≤ 35 días): `horas_sol` y `horas_tierra_visible` cuentan muestras con elevación > 0; `sol_el_media_deg` es la media solo de las horas con Sol arriba.

## 4. Resultados y hallazgos para el pitch

Tabla de diciembre de 2026 (la primera fila mensual de `metricas.csv`; los demás meses están en el CSV).

| Sitio | % Sol | Sol el. máx. | % Tierra |
|---|---|---|---|
| Apolo 11 | 47,85 | 88,09° | 100 |
| Apolo 15 | 49,19 | 62,67° | 100 |
| Apolo 17 | 47,58 | 68,55° | 100 |
| Shackleton | 100* | 1,74° | 39,11 |
| IM-1 (80,13° S) | 56,05 | 11,07° | 100 |
| Blue Ghost 1 | 47,58 | 70,13° | 100 |
| Chandrayaan-3 | 49,6 | 21,88° | 100 |
| Chang'e 4 | 49,73 | 45,98° | **0** |
| Chang'e 6 | 52,69 | 49,81° | **0** |
| SLIM | 47,98 | 77,92° | 100 |

\* Artefacto del horizonte ideal (ver §5).

- **Sol rasante en el polo:** en Shackleton el Sol nunca pasa de 1,74° sobre el horizonte, frente a unos 88° en Apolo 11. Sombras larguísimas y energía solar casi horizontal.
- **Tierra nunca visible:** Chang'e 4 y Chang'e 6 (cara oculta) tienen 0 h de Tierra; para comunicarse hace falta un satélite de relevo (Queqiao).
- **Misma luz, comunicación distinta:** Chang'e 4 y Apolo 15 tienen casi el mismo % de Sol (≈49 %) pero uno ve la Tierra todo el tiempo y el otro nunca.
- **Polo sur y Tierra:** en Shackleton la Tierra es visible solo el 39 % del mes (oscila entre −7° y 6°); en IM-1 (80° S) lo es siempre, pero con ≈10° de altura.
- **Cambio estacional en Shackleton (horizonte ideal):** el Sol pasa de 100 % en diciembre a 92,7 % en enero, 53,3 % en febrero y 0 % de marzo a mayo (elevación máxima −0,43° a −1,17°). Con un horizonte ideal, el punto se queda sin Sol; el Sol vuelve a ser rasante según la estación lunar. Es la base de HU-08 (mismo sitio en distintas fechas).
- **Chang'e 4:** la Tierra sigue en 0 % los 6 meses y el Sol se mantiene entre 47 y 52 %.
- **Ciclo de día lunar:** los sitios ecuatoriales y medios rondan el 48–50 % de Sol en 31 días; es la mitad del día lunar de 29,5 días.

## 5. Limitaciones

- **Horizonte ideal:** Horizons no modela relieve. Shackleton sale con 100 % de Sol, pero su fondo está en sombra permanente; el dato real corresponde a crestas cercanas. En IM-1 y Chandrayaan-3, con Sol bajo, el relieve local cambia mucho las horas reales. Hay que mostrar este aviso junto a la métrica y validar con PGDA 69/90 o QuickMap (HU-12).
- **Posiciones:** Blue Ghost 1 es el centro de la región de aterrizaje (±0,01°), no el punto de contacto. Shackleton es el centro del cráter, no un sitio elegido. La longitud de Chang'e 6 se convirtió de 206,0148° E.
- **Chandrayaan-3:** se usa la posición medida por LROC (−69,3741; 32,32). El objetivo planificado por ISRO es −69,367621; 32,348126 (folleto LVM3-M4); la diferencia es de unos centenares de metros.
- **Rango:** seis meses (dic 2026 a may 2027), no un año completo.
- **Predicción de la Tierra:** la cabecera de Horizons indica que los parámetros de orientación de la Tierra (EOP) son predicciones desde febrero de 2027; para un observador en la Luna el efecto es menor que 1 s y no cambia los datos a 6 decimales.
- **Trazabilidad:** no se guardaron las cabeceras crudas de Horizons; son reproducibles con `generar.py`.
- Citar «Fuente: NASA/JPL Horizons» sin sugerir respaldo de NASA al proyecto.

## 6. Revisión de datos
**APROBADO** para los 10 sitios con 6 meses (4369 filas por sitio) y 60 filas mensuales de métricas. Las efemérides coinciden con una descarga independiente de Horizons con diferencia máxima 0 en las 4369 filas, y las 60 filas de `metricas.csv` salen iguales al recalcularlas. Los meses tienen 672, 720 o 744 h: compara por porcentaje, no por horas.

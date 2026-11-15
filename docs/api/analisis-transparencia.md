# API · Transparencia del análisis · `GET /api/analisis/{site_id}`

Contrato del DTO para el frontend (sección «Cómo se analizan tus datos»). El esquema vive en `app/schemas/analisis.py`, el cálculo en `app/analysis/transparencia.py` y el contrato congelado en `tests/contrato/snapshots.json` (escenarios `analisis_*`).

## Petición
| Parámetro | Dónde | Obligatorio | Descripción |
|---|---|---|---|
| `site_id` | ruta | sí | Sitio del catálogo |
| `desde`, `hasta` | query | no | Rango UTC `YYYY-MM-DDTHH:MMZ` de la **serie** (por defecto: del primer dato a 90 días después del último). `desde` ≥ primer dato; `hasta` ≤ último dato + 366 días |
| `variable` | query | no | Variable de la serie: `sol_el_deg` (por defecto), `sol_az_deg`, `tierra_az_deg` o `tierra_el_deg` |

Errores, siempre `{"detail": "texto en español"}`: **404** (sitio o archivo de efemérides inexistente), **422** (fecha, rango o `variable` no válidos), **503** (los datos no cumplen el estándar o no hay `sites.csv`). Si falta la tabla de validación **no** es un error: `validacion.disponible` es `false` y dice por qué.

El análisis se calcula una vez por archivo y se guarda en caché con el `mtime` del CSV: si un usuario sube efemérides nuevas del sitio, la siguiente petición lo refleja. Respuesta típica: ≈ 35 KiB, 5 ms con caché y ≈ 0,2 s en frío.

## Respuesta
| Campo | Tipo | Qué es |
|---|---|---|
| `site_id`, `desde_utc`, `hasta_utc` | texto | Sitio y rango efectivo de la serie |
| `pasos[]` | 7 pasos | El pipeline: `carga`, `validacion_csv`, `features`, `fft`, `ajuste`, `validacion_horizons`, `proyeccion` |
| `pasos[].id/nombre/descripcion` | texto | Nombre y descripción breve en lenguaje claro, listos para pintar |
| `pasos[].estado` | `ok`, `advertencia` o `no_disponible` | `advertencia`: el paso es válido pero con reservas (p. ej. validación hecha con otros datos) |
| `pasos[].resultado[]` | `{nombre, valor, unidad}` | Medidas con etiqueta y unidad (`valor` es número, texto, booleano o `null`) |
| `entrada` | objeto | Archivo, `filas`, `bytes`, `inicio_utc`, `fin_utc`, `paso_horas`, `calidad` y `origen` |
| `entrada.calidad` | objeto | `filas_validas`, `filas_con_error` (0: un archivo con errores no se carga), `huecos`, `paso_constante` |
| `entrada.origen` | objeto | Ver abajo |
| `espectro` | objeto | `ventana_dias`, `resolucion_dias`, `sinodico_teorico_dias`, `sinodico_afinado_dias`, `series[]` (dos variables con sus `picos[]`: `periodo_dias`, `amplitud_rel` 0–1, `candidatos[]`, `es_sinodico`) y una `nota` |
| `modelo` | objeto | `terminos_por_cuerpo`, `periodos[]` (`cuerpo`, `periodo_dias`, `rol`), `ajuste_directo_elevacion_sol`, `r2{variable}` y `residuos{variable}` (`rmse_deg`, `mae_deg`, `sesgo_deg`, `max_abs_deg`, `p95_abs_deg`) |
| `validacion` | objeto | Ver abajo |
| `limitaciones[]` | `{codigo, mensaje, aplica}` | Limitaciones conocidas; `aplica` dice si afectan a ESTE sitio y estos datos |
| `serie` | objeto | `variable`, `nombre`, `paso_horas`, `fin_datos_utc` y `puntos[]` (≤ 301): `{utc, observado, ajustado, proyectado}` |

### `entrada.origen`: de dónde vienen los datos (solo se afirma lo demostrable)
| `tipo` | Cuándo | Texto sugerido |
|---|---|---|
| `semilla_nasa` | El SHA3-256 del archivo es idéntico al de la semilla del proyecto (generada con JPL Horizons) | «Datos de NASA/JPL Horizons» |
| `subido` | Difiere de la semilla y coincide con la última carga guardada que consta en la base de datos | «Subido por un usuario el …» (usa `ultima_carga.cargado_utc`) |
| `modificado` | Hay semilla o registro, pero el archivo no coincide con ninguno | «Difiere de la semilla y no hay una carga registrada» |
| `sin_registro` | No hay semilla ni registro con los que comparar | «Solo se puede dar la huella» |

`sha3_256` es siempre el del archivo actual. `ultima_carga` (`cargado_utc`, `archivo`, `sha3_256`, `coincide`, `cargado_por`) es `null` sin registro; `cargado_por` es «clave de API», «usuario registrado» o «anónimo» y **nunca** una identidad. `manifiesto` (`sha3_256`, `generado_utc`, `firmado`, `coincide`) es `null` si el manifiesto no lista el archivo. `coincide_con_semilla` es `null` si no hay semilla.

### `validacion`: cuánto acierta el modelo fuera de los datos
- `disponible: false` → no hay validación de este sitio (`motivo` lo explica); el resto va vacío.
- **`vigente`**: `true` si la validación se hizo con **estos mismos datos** (compara la huella). Si es `false`, los números son de **otros datos**: muéstralos como «de referencia», nunca como medidos con los datos del usuario (y la limitación `validacion_de_otros_datos` aplica).
- `rmse_deg`, `mae_deg`, `rmse_referencia_ingenua_deg` y `mejora_frente_a_ingenua` van por variable; `rmse_por_mes_deg` trae 12 valores por variable (mes de horizonte 1 a 12; puede haber `null`); `semaforo_aciertos` = `{meses, aciertos_energia, aciertos_comunicacion, aciertos_global}`; `puestas_sol` = `{eventos_reales, emparejados_pct, mae_h}` (puede ser `null` en los valores).
- Las cifras son un **RMSE de validación, optimista y no un intervalo de confianza**.

### Limitaciones (`codigo`)
`horizonte_ideal`, `no_sustituye_efemerides`, `ventana_corta`, `validacion_no_ciega` (siempre aplican), `sol_rasante_polo` (el Sol no pasa de 3°), `cenit_ecuador` (el Sol pasa de 85°) y `validacion_de_otros_datos`.

## Ejemplo real (recortado)
```json
{
 "site_id": "shackleton",
 "desde_utc": "2026-12-01T00:00Z",
 "hasta_utc": "2027-06-08T00:00Z",
 "pasos": [
  {
   "id": "carga",
   "nombre": "Carga del archivo",
   "descripcion": "Se lee el CSV de efemérides del sitio, con la misma lectura segura que usa la carga de datos.",
   "estado": "ok",
   "resultado": [
    {
     "nombre": "Filas leídas",
     "valor": 4369,
     "unidad": "filas"
    },
    {
     "nombre": "Tamaño del archivo",
     "valor": 257170,
     "unidad": "bytes"
    },
    "…"
   ]
  },
  {
   "id": "validacion_csv",
   "nombre": "Validación del archivo",
   "descripcion": "Se comprueba el estándar de CSV: cabecera, tipos, rangos, fechas en UTC, paso constante y saltos físicos.",
   "estado": "ok",
   "resultado": [
    {
     "nombre": "Filas válidas",
     "valor": 4369,
     "unidad": "filas"
    },
    {
     "nombre": "Filas con error",
     "valor": 0,
     "unidad": "filas"
    },
    "…"
   ]
  },
  {
   "id": "features",
   "nombre": "Cálculo de características con Polars",
   "descripcion": "Se miden las ventanas de luz solar y de Tierra visible, las horas de Sol por día lunar y la libración.",
   "estado": "ok",
   "resultado": [
    {
     "nombre": "Ventanas de Sol completas",
     "valor": 1,
     "unidad": ""
    },
    {
     "nombre": "Duración media de una ventana de Sol",
     "valor": 413.0,
     "unidad": "h"
    },
    "…"
   ]
  },
  "… (7 en total)"
 ],
 "entrada": {
  "archivo": "efemerides_shackleton.csv",
  "filas": 4369,
  "bytes": 257170,
  "inicio_utc": "2026-12-01T00:00Z",
  "fin_utc": "2027-06-01T00:00Z",
  "paso_horas": 1.0,
  "calidad": {
   "filas_validas": 4369,
   "filas_con_error": 0,
   "huecos": 0,
   "paso_constante": true
  },
  "origen": {
   "tipo": "semilla_nasa",
   "descripcion": "El archivo es idéntico a la semilla del proyecto, generada con JPL Horizons.",
   "sha3_256": "32f58cc4f530a72babb5a3c12918e166ec57527cccf60cb7378d8e36a9b7223b",
   "coincide_con_semilla": true,
   "ultima_carga": null,
   "manifiesto": null
  }
 },
 "espectro": {
  "ventana_dias": 182.0,
  "resolucion_dias": 4.7915,
  "sinodico_teorico_dias": 29.530589,
  "sinodico_afinado_dias": 29.525,
  "series": [
   {
    "variable": "sol_el_deg",
    "nombre": "Elevación del Sol",
    "picos": [
     {
      "periodo_dias": 29.5416,
      "amplitud_rel": 1.0,
      "candidatos": [
       "sinodico",
       "anomalistico",
       "…"
      ],
      "es_sinodico": true
     },
     {
      "periodo_dias": 206.3197,
      "amplitud_rel": 0.7437,
      "candidatos": [],
      "es_sinodico": false
     },
     "…"
    ]
   },
   {
    "variable": "tierra_az_deg",
    "nombre": "Azimut de la Tierra",
    "picos": [
     {
      "periodo_dias": 27.5155,
      "amplitud_rel": 1.0,
      "candidatos": [
       "sinodico",
       "anomalistico",
       "…"
      ],
      "es_sinodico": true
     },
     {
      "periodo_dias": 14.979,
      "amplitud_rel": 0.0972,
      "candidatos": [],
      "es_sinodico": false
     },
     "…"
    ]
   }
  ],
  "nota": "Con la ventana de datos disponible, la FFT no separa el mes sinódico (29,53 d) del anomalístico (27,55 d) ni del dracónico (27,21 d): por eso el modelo toma esos periodos físicos y la FFT solo los confirma."
 },
 "modelo": {
  "terminos_por_cuerpo": {
   "Sol": 56,
   "Tierra": 12
  },
  "periodos": [
   {
    "cuerpo": "Sol",
    "periodo_dias": 412.8761,
    "rol": "banda lateral 1·sinódico − anomalístico (incluye el batido de ~411 días)"
   },
   {
    "cuerpo": "Tierra",
    "periodo_dias": 31.8119,
    "rol": "evección: 2·sinódico − anomalístico"
   },
   "…"
  ],
  "ajuste_directo_elevacion_sol": true,
  "r2": {
   "sol_az_deg": 1.0,
   "sol_el_deg": 1.0,
   "tierra_az_deg": 0.9992,
   "tierra_el_deg": 0.9997
  },
  "residuos": {
   "sol_el_deg": {
    "rmse_deg": 0.006,
    "mae_deg": 0.0051,
    "sesgo_deg": -0.0,
    "max_abs_deg": 0.0157,
    "p95_abs_deg": 0.0112
   },
   "…": "…"
  }
 },
 "validacion": {
  "disponible": true,
  "vigente": true,
  "motivo": null,
  "validacion_desde_utc": "2027-06-01T00:00Z",
  "validacion_hasta_utc": "2028-06-01T00:00Z",
  "filas_entrenamiento_validadas": 4369,
  "rmse_deg": {
   "sol_az_deg": 1.5534,
   "sol_el_deg": 0.2178,
   "tierra_az_deg": 0.3893,
   "tierra_el_deg": 0.3982
  },
  "mae_deg": {
   "sol_az_deg": 1.2954,
   "sol_el_deg": 0.1798,
   "tierra_az_deg": 0.3253,
   "tierra_el_deg": 0.3069
  },
  "rmse_referencia_ingenua_deg": {
   "sol_az_deg": 1.9301,
   "sol_el_deg": 1.7991,
   "tierra_az_deg": 1.5382,
   "tierra_el_deg": 2.8742
  },
  "mejora_frente_a_ingenua": {
   "sol_az_deg": 1.2425,
   "sol_el_deg": 8.2603,
   "tierra_az_deg": 3.9512,
   "tierra_el_deg": 7.218
  },
  "rmse_por_mes_deg": {
   "sol_el_deg": [
    0.0463,
    0.1094,
    0.1666,
    0.1987,
    0.2181,
    0.2473,
    0.2736,
    0.2756,
    0.2574,
    0.2357,
    0.2242,
    0.2362
   ],
   "…": "…"
  },
  "semaforo_aciertos": {
   "meses": 12,
   "aciertos_energia": 12,
   "aciertos_comunicacion": 12,
   "aciertos_global": 12
  },
  "puestas_sol": {
   "eventos_reales": 4,
   "emparejados_pct": 25.0,
   "mae_h": 3.0
  }
 },
 "limitaciones": [
  {
   "codigo": "horizonte_ideal",
   "mensaje": "Horizonte ideal: se supone una Luna lisa. El relieve real (montañas, cráteres) puede tapar un Sol bajo.",
   "aplica": true
  },
  {
   "codigo": "no_sustituye_efemerides",
   "mensaje": "La proyección es una estimación estadística y no sustituye a las efemérides físicas de Horizons.",
   "aplica": true
  },
  "…"
 ],
 "serie": {
  "variable": "sol_el_deg",
  "nombre": "Elevación del Sol",
  "paso_horas": 16.0,
  "fin_datos_utc": "2027-06-01T00:00Z",
  "puntos": [
   {
    "utc": "2026-12-01T00:00Z",
    "observado": 1.1732,
    "ajustado": 1.1889,
    "proyectado": null
   },
   {
    "utc": "2027-06-07T16:00Z",
    "observado": null,
    "ajustado": null,
    "proyectado": -0.9424
   },
   "…"
  ]
 }
}
```

## Cómo presentarlo con honestidad
1. Enseña los siete pasos con su `estado`; un paso en `advertencia` o `no_disponible` debe verse distinto, no ocultarse.
2. El origen de los datos manda: si es `subido` o `modificado`, di claramente que **no** son datos de la semilla de NASA.
3. R² alto no es acierto fuera de los datos: el R² mide el ajuste dentro de ellos; el acierto lo miden la `validacion` y su `vigente`.
4. Pinta `limitaciones` con `aplica: true` de forma destacada y las demás, en segundo plano.
5. Los puntos de la serie con `proyectado` son una estimación estadística: distínguelos visualmente de `observado` y de `ajustado`.

# Scripts de datos lunares

Generan y validan los CSV de `data/` según `docs/plan/estandar-csv.md`.

- `sitios.py`: catálogo de sitios (coordenadas y fuente). Edita aquí para añadir o quitar sitios.
- `generar.py`: consulta JPL Horizons (Sol `10` y Tierra `399`, observador `coord@301`) y escribe `sites.csv`, `efemerides_<site_id>.csv` y `metricas.csv`.
- `validar.py`: comprobación básica del estándar y de las métricas.

## Regenerar las efemérides
Requiere Python 3 y red (solo biblioteca estándar). Desde esta carpeta:

    python3 generar.py            # escribe en ../../data
    python3 validar.py            # debe imprimir: ERRORES: ninguno

Rango fijo en `generar.py` (`INICIO`, `FIN`): 2026-12-01T00:00Z a 2027-06-01T00:00Z, paso 1 h (4369 filas por sitio). `metricas.csv` lleva una fila por sitio y mes. El horizonte es ideal (sin relieve). Ver `docs/datos/informe-fuentes.md`.

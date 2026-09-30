# Incidente: desbordamiento de `max_spread_pct` (2026-09-29)

> **Snapshot inmutable** del 2026-09-30. Convención en [`docs/README.md`](../README.md).

| Campo | Valor |
|---|---|
| Detectado | 2026-09-30, en la auditoría intermedia de [Phase 3.5-C2](../phases/PHASE_03_5_MARKET_VALIDATION.md) |
| Ocurrido | 2026-09-29, 02:15:04 → 03:41:10 UTC |
| Severidad | Alta: pérdida silenciosa de observaciones |
| Estado | **Corregido y validado en producción** el 2026-09-30 (migración 004 + aislamiento por mercado) |
| Código | `migrations/004_widen_max_spread_pct.sql`, `src/edgecourt/market/collector.py` |

## 1. Detección

La auditoría intermedia de C2 revisó el journal del collector y encontró **19 `ciclo fallido`**,
todos con el mismo error:

```
numeric field overflow
DETAIL:  A field with precision 8, scale 4 must round to an absolute value less than 10^4.
```

## 2. Causa raíz

- `market_observation.max_spread_pct` era `numeric(8,4)`: máximo **9.999,9999**.
  - Nota: la columna está en `market_observation`, **no** en `runner_price`.
- El spread del peor runner, `(lay_1 − back_1) / back_1 × 100`, puede ser mucho mayor en libros
  casi vacíos.
  - Con la escala de Betfair (1,01–1.000), la cota real es
    `(1000 − 1,01) / 1,01 × 100` = **98.909,9010 %**.
- **Mercado que lo provocó:** `1.263050661`, *Pe Marcinko v Frech* (WTA Beijing 2026, inicio
  publicado 2026-09-30 03:00 UTC).
  - Su libro tenía un spread del peor runner de **17.331,1927 %** (back 1,09 / lay 190) y
    **13.471,4286 %** (back 1,40 / lay 190), según los artefactos de C2.
- **Por qué solo en esa ventana:** su hito de 24 h (tolerancia de ±45 min) vencía entre las 02:15 y
  las 03:45 UTC. En cada ciclo de esa franja el collector intentaba guardar el hito y el `INSERT`
  desbordaba. Al cerrarse la tolerancia dejó de intentarlo y el fallo cesó.

## 3. Por qué un mercado afectó a los demás

- La transacción **ya era por mercado** (`with transaction(connection)` por libro): lo escrito
  antes del mercado inválido sí se confirmaba.
- Pero la excepción **no se capturaba dentro del bucle**. Salía de `for book in books` y de
  `run_cycle()`, así que ningún mercado posterior del ciclo, de ninguna etiqueta, llegaba a
  procesarse.
- Además, cada ciclo fallido activaba el **backoff** (`_wait_seconds` crece con los fallos
  consecutivos). Los ciclos se espaciaron a ~5 min y **todos** los mercados perdieron su cadencia de
  1 min cerca del inicio.
- Efecto colateral: el hueco entre dos keepAlive llegó a 19,0 min, a 1 min de la caducidad de la
  sesión `.es`. No llegó a caducar.

## 4. Impacto y datos perdidos

| Dato perdido | Cantidad | Recuperabilidad |
|---|---|---|
| Hito **24h** de `1.263050661` | 1 observación (nunca guardada) | **RECUPERABLE PARCIALMENTE.** C2 tiene los libros de ese mercado a las 02:30, 03:00 y 03:30 UTC (misma proyección `EX_BEST_OFFERS`, profundidad 3, datos retrasados), dentro de la tolerancia del hito. Son snapshots equivalentes de otro proceso y en otros instantes, no la captura exacta del collector |
| Observaciones adaptive de los mercados cercanos al inicio (`1.263050669`, `…684`, `…794`, `…795` y `…668`, `…670`, `…673`, `…678`, que se reprogramaron de 02:00 a 03:05) | **Entre 16 y ~48** (estimación: cadencia real de ~5 min frente a la esperada de 1 min) | **RECUPERABLE PARCIALMENTE** a resolución de 30 min desde los artefactos de C2 (libros de todos esos mercados a las 02:00, 02:30, 03:00 y 03:30). A resolución de 1 min: **NO RECUPERABLE** |
| Otros hitos en la ventana | 0: los hitos 24h de otros 10 mercados se guardaron a las 02:15, y los 1h/10m/close se capturaron | — |

**Fuentes consideradas para recuperar datos:**
- **Logs:** solo contienen el error, no el libro. No sirven.
- **Artefactos de C2:** libros brutos cada 30 min. Recuperación parcial.
- **Betfair a posteriori:** la API no sirve libros históricos. Betfair Historical Data es de pago y
  del exchange internacional, no del `.es`, y no es una fuente del proyecto. No se considera.

**No se ha hecho ningún backfill.** Cualquier incorporación de datos de C2 a `market_observation`
requiere autorización y una decisión documentada, porque mezclaría dos procesos de captura
distintos.

## 5. Corrección

### 5.1 Esquema: migración `004_widen_max_spread_pct.sql`

```sql
ALTER TABLE market_observation ALTER COLUMN max_spread_pct TYPE numeric(12,4);
```

- **`numeric(12,4)`:** hasta 99.999.999,9999, más de 1.000 veces la cota real.
- **Misma escala de 4 decimales:** los datos, las consultas y las exportaciones no cambian.
- **Descartado `double precision`:** redondeo binario en un dato hoy exacto.
- **Descartado `numeric` sin precisión:** pierde la escala fija.
- **Sin clamp:** se guarda el valor real. Un spread no calculable sigue siendo `NULL`.
- **Rendimiento:** ampliar la precisión sin cambiar la escala no reescribe la tabla. El `ALTER`
  sobre la tabla padre se propaga a las particiones.
- **Migraciones anteriores:** no se han editado.

### 5.2 Aislamiento por mercado (`collector.run_cycle`)

```python
try:
    with transaction(connection) as cursor:
        save_observation(cursor, payload)
except (psycopg.DataError, psycopg.IntegrityError) as exc:
    result.failed_markets.append(payload.market_id)
    log.error("observacion rechazada por PostgreSQL", extra={"market_id": ..., ...})
    continue
```

- **Resultado:** mercado A correcto → persiste; B rechazado → solo se deshace la transacción de B;
  C correcto → persiste.
- **El ciclo no falla:** no hay backoff ni se pierde la cadencia del resto.
- **El fallo queda registrado** con `market_id`, etiqueta, tipo y primera línea del error, sin
  datos sensibles. `CycleResult.failed_markets` aparece en cada «ciclo completado».
- **Solo se aíslan errores de DATOS** (`DataError`, que incluye el desbordamiento, e
  `IntegrityError`, que incluye los CHECK).
  - Los errores de **conexión** (`OperationalError`, `InterfaceError`) siguen abortando el ciclo.
    Con la conexión rota no tiene sentido seguir, y el bucle principal la recupera como antes.
- **Idempotencia sin cambios:** el mercado rechazado se reintenta en el siguiente ciclo con la
  misma `capture_key`.
- Sin colas, Redis ni cambios de arquitectura.

## 6. Tests de regresión

| Test | Qué demuestra |
|---|---|
| ★ `test_extreme_spread_is_stored_without_clamp` (integración, 3 casos) | Los valores del incidente (17.331,1927 y 13.471,4286) y la cota máxima (98.909,9010) se guardan **exactos** en PostgreSQL, sin desbordamiento, truncado ni clamp |
| `test_max_spread_pct_column_is_numeric_12_4` (integración) | Tipo final de la columna |
| ★ `test_migration_004_upgrades_an_existing_database_preserving_data` (integración) | Ruta real: esquema 001–003 con datos → se aplica 004 → datos intactos |
| ★ `test_invalid_market_is_rolled_back_alone` (integración) | A válido, B rechazado por un CHECK real, C válido: se guardan A y C; ni la observación ni los precios de B; un ciclo posterior sigue funcionando |
| ★ `test_a_rejected_market_does_not_lose_the_others` | Lo mismo con un `NumericValueOutOfRange` simulado |
| `test_integrity_errors_are_also_isolated` | Los errores de integridad también se aíslan |
| `test_rejected_market_is_logged_with_its_market_id` | El registro identifica el `market_id` |
| ★ `test_connection_errors_still_abort_the_cycle` | Un `OperationalError` sigue abortando el ciclo |
| ★ `test_collector_keeps_running_after_a_rejected_market` | 3 ciclos seguidos sin contar como fallos |
| ★ `test_max_spread_pct_is_widened_by_a_new_migration` (sin BD) | La corrección va en una migración nueva, sin clamp; la 001 queda intacta |

★ = `critical`.

**Suite:** **515 passed**, de ellos 174 críticos y 41 de integración contra PostgreSQL de test.
`ruff` limpio. Barrera de solo lectura y claves reservadas del logging en verde.

## 7. Otras columnas revisadas

No hay otra columna con riesgo demostrado de desbordamiento:
- precios `numeric(10,3)`, con máximo 1.000 en Betfair;
- tamaños, `total_available` y `total_matched` `numeric(14,2)`;
- `minutes_to_start` `numeric(10,2)`;
- `handicap` `numeric(6,2)`.

En las tablas de predicción y ledger, todavía vacías, las probabilidades, `edge` y `clv` en
`numeric(8,6)` están acotadas por definición. Habrá que revisar `expected_value numeric(10,6)`
cuando se implemente el Value Engine, pero hoy no tiene riesgo demostrado.

## 8. Validación en producción (2026-09-30)

| Paso | Resultado |
|---|---|
| `edgecourt db migrate` | a las 05:32:38 UTC: **aplicada 004**, versión de esquema 4, 0 pendientes |
| Tipo tras la migración | `numeric(12,4)` en `market_observation`, `market_observation_202609` y `market_observation_default` |
| Integridad de datos | 3.134 observaciones; suma de control de `max_spread_pct` idéntica antes y después (132.037,3725; máximo 6.762,7451) |
| Reinicio controlado del collector | a las 05:35:29 UTC (lo ejecutó el responsable del proyecto): nuevo PID, `sesion iniciada` `.es`, ciclos con `failed_markets: []`, es decir, código nuevo cargado |
| `edgecourt collector health` | OK |
| `numeric field overflow` desde la migración | 0 |
| Timer de C2 | sigue `active`; su código no se ha tocado |

## 9. Lecciones

- Un límite de tipo en una métrica **derivada** no debe poder tumbar la captura de los datos
  brutos: ahora un fallo de datos queda confinado al mercado que lo produce.
- El backoff protege ante caídas, pero amplifica un fallo determinista y repetido. Con el
  aislamiento, un fallo de datos ya no cuenta como ciclo fallido.
- La auditoría de C2 detectó el problema precisamente porque guarda los libros brutos en paralelo.

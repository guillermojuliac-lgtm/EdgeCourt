# PostgreSQL — almacenamiento operativo

> `migrations/001_initial_schema.sql` cita un `docs/PERSISTENCE.md` que nunca llegó a crearse.
> **Este documento cumple esa función.** No se ha cambiado la migración: las migraciones
> aplicadas no se editan.

Decisiones relacionadas: [DEC-004](../DECISIONS.md#dec-004), [DEC-005](../DECISIONS.md#dec-005),
[DEC-007](../DECISIONS.md#dec-007), [DEC-014](../DECISIONS.md#dec-014) y
[DEC-016](../DECISIONS.md#dec-016).

## Reparto de responsabilidades

| | PostgreSQL | Parquet |
|---|---|---|
| Rol | estado operativo 24/7 | analítico, histórico y archivo |
| Fuente de verdad de | catálogo y observaciones de Betfair; predicciones, paper bets y liquidación (futuras) | dataset histórico de tenis (`match_facts`), Elo y features |
| Escritura | pequeña, transaccional e idempotente | por lotes, inmutable |
| Betfair | destino **único** del collector | solo por exportación (`edgecourt db export-parquet`) |

**Nada se escribe dos veces.**

## Instancia

| Elemento | Valor verificado el 2026-09-27 |
|---|---|
| Versión | PostgreSQL 18.6 (Ubuntu), nativa, servicio `postgresql.service` |
| Base | `edgecourt`, con un rol de aplicación dedicado (sus privilegios no se han auditado) |
| Puerto | no estándar. El 5432 lo ocupa otro PostgreSQL en contenedor, y `scripts/setup_postgres.sh` detecta el puerto del clúster |
| Conexión | `DATABASE_URL` en el `.env`. **Nunca** se documenta ni se registra en logs con la contraseña; `redact_dsn()` la oculta |
| Base de tests | `EDGECOURT_TEST_DSN`, **distinta por validación**: los tests de integración recrean el esquema |
| Migraciones aplicadas | 001, 002 y 003 (2026-09-18 15:14 CEST); **004** (2026-09-30 07:32 CEST) |

Scripts: `scripts/setup_postgres.sh` (creación inicial) y `scripts/diagnose_postgres.sh`.

## Esquema

### Catálogo (se conserva siempre)

| Tabla | Clave | Notas |
|---|---|---|
| `betfair_event` | `event_id` | competición, país, `open_date` |
| `betfair_market` | `market_id` | `market_start_time` guarda el **último valor visto**. Los valores anteriores se reconstruyen como `observed_at + minutes_to_start` de cada observación |
| `betfair_runner` | `(market_id, selection_id)` | `player_id` **nullable y sin FK**, a la espera de Phase 8b. Nunca se rellena con un fuzzy match |

### Serie temporal (particionada por mes)

| Tabla | Contenido |
|---|---|
| `market_observation` | **Una fila cada vez que se observa un mercado**, tenga precios o no |
| `runner_price` | Precios BACK/LAY de 3 niveles por runner. **Solo** existe si hubo precios |

Columnas clave de `market_observation`:

| Columna | Significado |
|---|---|
| `observed_at` | Timestamp real de la observación, nunca el planificado |
| `snapshot_label` | Hito al que apuntaba la captura (`24h`… `close`) o `adaptive` |
| `capture_key` | Clave de idempotencia: el nombre del hito, o `a:YYYYMMDDHHMM` alineado a la rejilla de la cadencia |
| `minutes_to_start` | Distancia real al inicio **publicado en ese momento** |
| `has_prices` / `has_liquidity` | «Hay algo». El umbral de negocio se aplica al consultar |
| `total_available` | Suma de los tamaños de los niveles 1–3, BACK + LAY, de todos los runners |
| `max_spread_pct` | Peor `(lay_1 − back_1)/back_1 × 100` entre runners con ambos lados. **`numeric(12,4)` desde la migración 004** (antes `numeric(8,4)`, que desbordaba por encima de 9.999,9999). La cota real con la escala de Betfair es 98.909,9010 %. `NULL` si no es calculable; sin clamp |
| `collector_run_id` | Ejecución que escribió la fila |

Restricciones que impiden estados incoherentes:
- `observation_liquidity_implies_prices` y `observation_prices_imply_runners`;
- `runner_price_back_valid` y `runner_price_lay_valid` (cuota ≥ 1,01);
- índice único `(market_id, capture_key, observed_at)`, para la idempotencia.

**Particiones:**
- `ensure_month_partition()` crea `<tabla>_YYYYMM` de forma idempotente. Existe
  `market_observation_202609`.
- Las particiones `*_default` recogen lo que caiga fuera de un mes creado.
- El motivo es poder archivar con DETACH + exportar + DROP, en lugar de un `DELETE` masivo.

### Modelos, predicciones y paper betting (tablas creadas, vacías a 2026-09-27)

| Tabla | Garantía |
|---|---|
| `model_version` | Como máximo un modelo en `production` (índice único parcial). `promotion_requires_who` |
| `prediction` | Referencia a la observación **lógica, sin FK**, para poder purgar las observaciones y conservar las predicciones |
| `paper_bet` | Solo inserción: `RULE ... DO INSTEAD NOTHING` para UPDATE y DELETE, cadena `prev_hash` / `row_hash` y libro congelado en `jsonb` |
| `bet_settlement` | Igual que `paper_bet`. La liquidación nunca modifica la apuesta |
| `match_result` | `void_reason`: una anulación no es una pérdida |

### Vistas

- `liquidity_emergence`: curva de aparición de liquidez, en 48 tramos de 30 min sobre 24 h.
- `snapshot_coverage`: cobertura por etiqueta. La usa `edgecourt collector status`.

## Retención y archivo (no activada)

- `RETENTION_DAYS` = 90 en `config.py`.
- `archive_run` registra cada exportación: recuentos en BD y en fichero, SHA-256 y `verified`.
- Los CHECK impiden:
  - marcar como verificada una exportación cuyos recuentos no cuadran;
  - registrar una purga sin verificación previa.
- `assert_archived(tabla, partición)` lanza una excepción si no hay exportación verificada.
- **Estado a 2026-09-27:** 0 filas en `archive_run` y **no existe comando de purga**. Nada se ha
  purgado.

## Operaciones (CLI)

```bash
uv run edgecourt db migrate          # aplica migraciones pendientes (transaccionales)
uv run edgecourt db status           # versión de esquema y recuento de filas
uv run edgecourt db liquidity        # vista liquidity_emergence
uv run edgecourt db export-parquet   # exportación verificada; no borra nada
uv run edgecourt db import-parquet   # migración idempotente de los Parquet antiguos (18-sep)
```

**Reglas de las migraciones:**
- No incluyen `BEGIN`/`COMMIT`: la transacción la gestiona el runner, que rechaza cualquier
  migración con control de transacción propio.
- Una migración aplicada no se edita. Los cambios van en una migración nueva.

## Consultas de auditoría

Para analizar sin riesgo, usar una sesión con `SET default_transaction_read_only = on`. Así se
hizo en la [auditoría de la Semana 1](../audits/2026-09-week1-market-audit.md); su script
`q.py` es un ejemplo reutilizable.

## Transacciones y aislamiento de fallos

- **Una transacción por mercado:** observación y precios se escriben juntos o no se escribe
  ninguno.
- **Desde el 2026-09-30, un error de *datos* de un mercado** (`psycopg.DataError`,
  `IntegrityError`) solo deshace esa transacción. El collector registra el `market_id` en
  `failed_markets` y sigue con los demás mercados del ciclo.
- **Los errores de *conexión*** siguen abortando el ciclo.
- Ver el [incidente de desbordamiento](../audits/2026-09-spread-overflow-incident.md).

## Incidentes históricos relevantes

- **2026-09-29, desbordamiento de `max_spread_pct`:** 19 ciclos fallidos y pérdida parcial de
  observaciones. Corregido con la migración 004 y el aislamiento por mercado
  ([informe](../audits/2026-09-spread-overflow-incident.md)).

- **2026-09-18, commit `7eea268`:** la contraseña de la base de tests apareció en claro en un
  traceback de pytest, porque la fixture recibía la DSN como argumento. Se corrigió con `SafeDsn`.
  El commit indica que **esa contraseña debe rotarse**; no está verificado que se haya hecho (ver
  [problemas conocidos](../PROJECT_STATUS.md#problemas-conocidos)).

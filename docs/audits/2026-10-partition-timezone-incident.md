# Incidente: particiones con límites en Europe/Madrid (2026-10-01)

> **Snapshot inmutable** del 2026-10-05. Convención en [`docs/README.md`](../README.md).

| Campo | Valor |
|---|---|
| Ocurrido | 2026-10-01 00:00:42 UTC → 2026-10-05 13:16 UTC |
| Detectado | 2026-10-05 ~12:55 UTC, al revisar la salud del collector durante el estudio C2 |
| Severidad | **Crítica**: el collector estuvo **109,6 h** (4 días, 13 h y 36 min) sin persistir ninguna observación |
| Estado | **Corregido y validado en producción** el 2026-10-05 |
| Código | `migrations/005_utc_monthly_partitions.sql`, `src/edgecourt/db/repositories.py`, `src/edgecourt/cli.py` |
| Decisión derivada | [DEC-019](../DECISIONS.md#dec-019): particionado mensual en UTC explícito |

## 1. Cronología (UTC)

| Cuándo | Qué |
|---|---|
| 2026-09-18 | La migración 002 define `ensure_month_partition` con `FOR VALUES FROM (%L) TO (%L)` sobre fechas sin hora ni zona |
| 2026-09-18 | El collector crea `market_observation_202609` en una sesión `Europe/Madrid`: `[2026-08-31 22:00, 2026-09-30 22:00)` en UTC |
| **2026-09-30 22:00** | La partición de septiembre termina. Hasta las 23:40, **32 observaciones y 64 precios** caen en `default` **sin error** |
| **2026-10-01 00:00:42** | Cambia el mes UTC. El collector intenta crear octubre con las fechas `'2026-10-01'` en hora de Madrid, es decir, **desde el 30-sep 22:00 UTC**, lo que solapa con las filas de `default`. PostgreSQL lo rechaza. **Primer ciclo fallido** |
| 2026-10-01 → 2026-10-05 | Cada ciclo falla igual, cada ~5 min por el backoff: **1.311 ciclos fallidos**. El proceso sigue vivo y `systemd` lo ve `active` |
| 2026-10-05 ~12:55 | `collector health` indica «CON AVISOS» y 109 h sin observaciones. Se diagnostica la causa |
| 2026-10-05 13:12:00 | Se aplica la migración 005 en producción |
| 2026-10-05 13:16:01 | El collector, **sin reiniciarse**, completa su primer ciclo (ya usa la función SQL corregida) |
| **2026-10-05 13:17:01** | **Primera observación nueva** (id 3482), en `market_observation_202610` y con sus precios |

## 2. Causa raíz (confirmada en producción)

| | Hecho | Evidencia |
|---|---|---|
| **A** | Los límites de las particiones se crearon en `Europe/Madrid` | `FROM ('2026-09-01 00:00:00+02') TO ('2026-10-01 00:00:00+02')`, es decir, `[2026-08-31 22:00:00+00, 2026-09-30 22:00:00+00)`, igual en `market_observation_202609` y `runner_price_202609` |
| **B** | El mes se elegía en UTC | `ensure_partitions(cursor, now)` pasaba `now.date()` con `now` en UTC |
| **C** | Había filas atrapadas en `default` | 32 observaciones (ids 3450–3481) y 64 precios, entre las 22:10:25 y las 23:40:39 UTC del 30-sep |
| **D** | El solapamiento impedía crear octubre | `CheckViolation: updated partition constraint for default partition "market_observation_default" would be violated by some row`, al ejecutar `CREATE TABLE market_observation_202610 PARTITION OF market_observation FOR VALUES FROM ('2026-10-01') TO ('2026-11-01')` |

> **Matiz respecto al planteamiento inicial.** Las filas atrapadas **no son de octubre UTC**: son
> del **30-sep UTC** y solo son «octubre» en hora de Madrid (00:10–01:40 CEST). Con límites UTC
> **no habría habido solapamiento**: octubre habría empezado a las 00:00 UTC y esas filas, que
> terminan a las 23:40, no lo habrían bloqueado.

La zona de la sesión era `Europe/Madrid` en el servidor. Las fechas `date` sin zona se convierten a
`timestamptz` con la zona de la sesión, así que el desfase era de dos horas (CEST).

## 3. Impacto y datos afectados

- **Periodo sin observaciones persistidas:** del **2026-09-30 23:40:39 UTC** al **2026-10-05
  13:17:01 UTC** = **109,6 h** (≈ 6.576 ciclos normales de 60 s).
- **Ciclos fallidos:** 1.311, del 2026-10-01 00:00:42 al 2026-10-05 13:11:01 UTC.
- **Datos perdidos (estimación):**
  - entre ~1.700 y ~2.300 observaciones, extrapolando las 384–501 al día del 29 y 30 de septiembre
    (probablemente más, por ser semana de WTA 1000 y ATP Masters);
  - unos **600 hitos** (6 por mercado, unos 100 mercados que empezaron en la ventana);
  - la sincronización del catálogo: los eventos y mercados que aparecieron y terminaron en la
    ventana **no están** en `betfair_event`, `betfair_market` ni `betfair_runner`.
- **Datos existentes:** ninguno se perdió. Las 3.462 observaciones y los 4.973 precios anteriores
  se conservan íntegros (ver §6).
- **Efectos colaterales:**
  - la comparación «audit frente a collector» de C2 quedó bloqueada desde el 1-oct;
  - `collector health` mostraba la hora local etiquetada como UTC («01:40:39» en lugar de las
    23:40:39 UTC), lo que dificultó el diagnóstico.

## 4. Por qué no se detectó antes

**Por qué `systemd` seguía en `active`:**
- La unidad es `Type=simple` con `Restart=on-failure`. Solo reinicia si el proceso **sale** con
  error.
- El bucle del collector captura **cada excepción de ciclo** a propósito («un ciclo no puede matar
  el proceso»): solo cuenta fallos consecutivos y aumenta la espera (tope de 300 s). Un fallo
  determinista nunca termina el proceso.
- No hay `Type=notify` ni `WatchdogSec`: systemd no sabe si el collector **persiste**, solo si
  **vive**.

**Por qué nadie lo vio:**
- `collector health` sí avisaba («32 filas en la partición por defecto», y desde las 02:40 UTC del
  1-oct, «la última observación es de hace X h») y salía con código 1. **Nada lo ejecuta ni lo
  vigila**: no hay alertas (Telegram está pendiente) ni ninguna unidad que lo use.
- El aislamiento por mercado de [2026-09-30](2026-09-spread-overflow-incident.md) no ayuda aquí: el
  fallo ocurre en la sincronización del catálogo, antes del bucle de mercados.

**Por qué no lo cazaron los tests:**
- Todos los tests de integración trabajaban dentro de un mismo mes, con la partición creada para
  ese mes, y ninguno cruzaba un cambio de mes.
- Ninguno comprobaba los límites reales de una partición ni forzaba una sesión con zona distinta
  de UTC.

## 5. Reparación

### 5.1 Migración `005_utc_monthly_partitions.sql`

Una sola migración transaccional (la gestiona el runner; si algo falla, se revierte **todo**,
incluida su anotación en `schema_migration`):

1. **Funciones:** `utc_month_bounds(fecha)`, `partition_bounds(partición)` y la nueva
   `ensure_month_partition`, que escribe los límites con `+00` explícito y avisa si encuentra una
   partición con límites no UTC.
2. **Reparación**, solo si hay filas en `default` o particiones con límites no UTC:
   1. bloquea `market_observation` y `runner_price`;
   2. **preserva** las filas atrapadas en tablas temporales de la misma transacción (con
      recuento y md5);
   3. las **retira** de `default`; los precios caen en cascada con su observación;
   4. suelta la FK raíz `runner_price → market_observation`, desacopla **todas** las particiones
      mal acotadas y las **reacopla con límites UTC**, y recrea la FK con su definición exacta;
   5. crea las particiones UTC de los meses de las filas retiradas;
   6. **reinserta** las filas con `OVERRIDING SYSTEM VALUE` (conservan ids y contenido);
   7. **verifica**: recuentos totales, md5 del contenido reinsertado, `default` vacío y límites
      UTC en todas las particiones. Cualquier discrepancia aborta y revierte.
3. **Mes actual y siguiente (UTC)** para ambas tablas.

Notas de diseño:
- La FK raíz se suelta porque PostgreSQL no deja desacoplar una partición de
  `market_observation` mientras `runner_price` la referencia
  (`removing partition ... violates foreign key constraint`). El test de la reparación real lo
  descubrió.
- Desacoplar **todas** antes de reacoplar evita solapamientos intermedios si septiembre y octubre
  tienen límites de Madrid (p. ej. si se hubiera desplegado antes el código nuevo con la función
  antigua).
- Si una partición antigua tiene filas fuera de su mes UTC, la migración **aborta** pidiendo
  intervención manual: nunca mueve datos que no ha preservado.
- No usa `TRUNCATE`, no reconstruye ids ni inventa datos. La secuencia de identidad no se toca.

### 5.2 Código

- `repositories.ensure_partitions` calcula el mes en **UTC** y garantiza **el mes actual y el
  siguiente**. Un `datetime` sin zona se interpreta como UTC.
- `collector health`: `format_utc`/`format_age` convierten a UTC real y terminan en `Z`.

## 6. Política UTC definitiva ([DEC-019](../DECISIONS.md#dec-019))

Toda partición mensual es `[YYYY-MM-01 00:00:00 UTC, mes siguiente 00:00:00 UTC)`, con límites
`+00` explícitos. No depende de la zona del servidor, de la sesión, de Europe/Madrid ni del horario
de verano.

Particiones resultantes en producción:

| Partición | Límites (UTC) |
|---|---|
| `market_observation_202609` y `runner_price_202609` | `[2026-09-01 00:00, 2026-10-01 00:00)` (reajustadas) |
| `market_observation_202610` y `runner_price_202610` | `[2026-10-01 00:00, 2026-11-01 00:00)` |
| `market_observation_202611` y `runner_price_202611` | `[2026-11-01 00:00, 2026-12-01 00:00)` (por adelantado) |

## 7. Tests

**+31 tests** (546 en total; 192 críticos, 63 de integración contra PostgreSQL):

| Fichero | Qué cubre |
|---|---|
| `tests/test_partitioning_utc.py` (21) | Límites UTC exactos en 5 zonas de sesión (UTC, Madrid, Los Ángeles, Calcuta, Auckland); mes actual y siguiente; idempotencia; mes elegido en UTC y no en la zona del `datetime`; cambio **septiembre → octubre** con sesión Madrid y filas de 22:00 a 00:00 UTC (ninguna cae en `default`); **octubre se puede crear aunque `default` tenga filas de septiembre**; cambios de horario de verano (25-oct y 29-mar); cambio de año **diciembre → enero**; aviso ante límites no UTC |
| | **Reproducción exacta del incidente** (esquema 001–004, septiembre en hora de Madrid, filas atrapadas, y fallo `CheckViolation` al crear octubre) y la reparación: ANTES = DESPUÉS en recuento y md5, ids conservados, `default` vacío, septiembre en UTC, relaciones observación → precios, índices y FK intactos, la secuencia de identidad no retrocede |
| | Reversión completa si una verificación falla; idempotencia sobre un esquema ya correcto; particiones vecinas con límites de Madrid |
| `tests/test_collector_health_utc.py` (9) | `format_utc`/`format_age`; la CLI muestra UTC real y no la hora local; caso con sesión PostgreSQL en Madrid |
| `tests/test_db_migrate.py` (+1) | La política UTC queda fijada y la migración 002 conserva su historia |

El test existente de la migración 004 se actualizó para no suponer que la 004 es la última.

## 8. Validación

**Ensayo previo con una copia real de producción** (`pg_dump` de solo lectura restaurado en la base
de test): la migración dio ANTES = DESPUÉS con los mismos md5 que producción. Producción no se
tocó.

**Producción, 2026-10-05 13:12 UTC** (`edgecourt db migrate`):

| | Antes | Después |
|---|---|---|
| `market_observation` | 3.462 filas, md5 `e188fcdc346cefd4605ff92a585785be` | 3.462 filas, **mismo md5** |
| `runner_price` | 4.973 filas, md5 `e755c6b9db7654b023e89801dc9800f0` | 4.973 filas, **mismo md5** |
| `market_observation_default` / `runner_price_default` | 32 / 64 | **0 / 0** |
| Máximo `observation_id` y secuencia | 3.481 / 3.481 | 3.481 / 3.481 |
| Versión de esquema | 4 | 5 |

- Las **32 observaciones y los 64 precios** atrapados siguen existiendo con el mismo id (3450–3481)
  y el mismo instante, ahora en `market_observation_202609` y `runner_price_202609`.
- 0 precios huérfanos, 0 observaciones con precios que falten, 0 duplicados de
  `(market_id, capture_key, observed_at)`.
- Copia de seguridad previa de esas 96 filas fuera de git, en
  `data/backups/2026-10-partition-repair/` (permisos 600).
- **Recuperación:** el collector, sin reiniciar, completó su primer ciclo a las 13:16:01 UTC y la
  primera observación nueva fue a las 13:17:01 UTC. Desde la migración: **0 ciclos fallidos**, 0
  errores de partición y 0 `numeric field overflow`.
- **C2** sigue `active` y sin cambios (3 ejecuciones `ok` en la hora siguiente).

## 9. Ventana perdida y recuperabilidad (sin backfill)

**No se ha hecho ningún backfill.** Cualquier incorporación de datos de C2 a `market_observation`
exige autorización y una decisión documentada: mezcla dos procesos de captura distintos.

Periodo: **2026-09-30 23:40:39 UTC → 2026-10-05 13:17:01 UTC** (109,6 h).

C2 no usa PostgreSQL y no se vio afectado. En esa ventana tiene **218 de 218 slots** (cada 30 min),
**131 mercados** y **7.263 libros** (mercado × slot).

| Qué | Clasificación |
|---|---|
| Observaciones a resolución de 1 min (cadencia *adaptive* cerca del inicio) | **NO RECUPERABLE** |
| Hitos `10m` y `close` (tolerancia de ±3 y ±2 min) | **NO RECUPERABLE** de forma exacta. C2 los cubre en solo 29 y 20 de 100 mercados, de pura casualidad |
| Hitos `24h`, `12h`, `6h` y `1h` | **RECUPERABLE PARCIALMENTE**: un libro de C2 dentro de la tolerancia en 58, 82, 91 y 82 de 100 mercados. Es un snapshot equivalente (misma proyección, datos retrasados) de otro proceso y en otro instante, no la captura exacta |
| Catálogo (eventos, mercados y runners) de lo que apareció en la ventana | **RECUPERABLE** a resolución de 30 min desde los catálogos de C2. El `first_seen_at` exacto no |
| Precios de mercados a media hora entre slots | **NO RECUPERABLE** |

Ninguna observación de la ventana es **recuperable exactamente**.

## 10. Relación con C2

- C2 no se vio afectado y hoy es el **único registro continuo** del 1 al 5-oct.
- La comparación «audit frente a collector» quedó acotada: de los 155 mercados vistos, los **40**
  que el collector pudo ver antes de caerse aparecen todos en su base y los otros **115** aparecieron
  después. No hay ningún problema de descubrimiento.
- Desde la recuperación, la comparación vuelve a ser posible para los mercados nuevos.

## 11. Monitorización futura (propuesta, **no implementada**)

La restauración y la monitorización se tratan por separado.

Qué debería detectarse: «proceso vivo, pero el collector no persiste». Condición: **varios ciclos
fallidos seguidos**. No basta con «sin observaciones recientes»: hay periodos legítimos sin
mercados (el 21-sep no hubo ninguno y el collector funcionaba).

**Propuesta mínima: watchdog de systemd ligado a los ciclos con éxito.**
1. El collector envía `WATCHDOG=1` a `$NOTIFY_SOCKET` tras cada ciclo **completado**. Un ciclo
   sin mercados cuenta como éxito. Son ~15 líneas de `socket` de la biblioteca estándar, sin
   dependencias.
2. La unidad pasa a `Type=notify`, `NotifyAccess=main` y `WatchdogSec=900`.
3. Un fallo determinista (como este) deja de enviar `WATCHDOG=1`: systemd mata y reinicia el
   proceso. Tras 5 reinicios en 10 minutos (`StartLimit`), la unidad queda **`failed`**: visible en
   `systemctl --failed`, en el journal y utilizable mañana con `OnFailure=` para Telegram.

Alternativa más simple, pero con falsas alarmas: un timer que ejecute `collector health` y falle
(código 1). Es independiente del collector, pero salta también en los periodos legítimos sin
mercados.

Complementos baratos, para otra tarea: que `collector health` avise si no existe la partición del
mes siguiente o si alguna no tiene límites UTC.

## 12. Pendiente

- **Reinicio controlado** del collector para cargar el código Python nuevo (`ensure_partitions` del
  mes siguiente). No es urgente: el arreglo real está en la base de datos (función SQL), que ya
  usa el proceso en marcha.
- **Decidir** si se hace backfill parcial desde C2 y con qué criterio.
- **Decidir** la monitorización (§11).

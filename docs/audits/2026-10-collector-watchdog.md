# Hardening: watchdog de systemd para el collector (2026-10-05)

> **Snapshot inmutable** del 2026-10-05. Convención en [`docs/README.md`](../README.md).

| Campo | Valor |
|---|---|
| Origen | [Incidente de particiones](2026-10-partition-timezone-incident.md) (§11, «`active` no significa persistiendo») |
| Estado | **VALIDADO en producción el 2026-10-05** (DONE) |
| Código | `src/edgecourt/systemd_notify.py`, `src/edgecourt/market/collector.py`, `src/edgecourt/db/health.py`, `deploy/edgecourt-collector.service` |
| Commit | `feat: add systemd watchdog for collector` (corrección previa de las particiones: `4ca87d0`) |

## 1. Auditoría previa

| Elemento | Hallazgo |
|---|---|
| Bucle (`Collector._loop`) | Cada iteración: `run_cycle()` → `except Exception` → espera. El `except` absorbe **todo** salvo `MissingCredentialsError`, por lo que un fallo determinista no termina nunca el proceso: es el hueco del incidente |
| Fin de un ciclo sano | `run_cycle()` vuelve sin excepción. Incluye volver pronto con 0 mercados o 0 capturas debidas. Los mercados rechazados por PostgreSQL (`DataError`/`IntegrityError`) se aíslan por mercado y **no** abortan el ciclo |
| Backoff | `min(intervalo·2^n, 300 s)`; desde el 5.º fallo, 300 s. Se mantiene sin cambios |
| systemd | `Type=simple`, `Restart=on-failure`, `RestartSec=60`, `StartLimit` 5/600 s. Con `simple`, `active` solo indica que el proceso vive |
| Apagado | SIGTERM/SIGINT activan `_stop`; `run()` cierra Betfair y la conexión en `finally` |
| `collector health` | Detecta la falta de escritura, pero no lo ejecuta nadie |

## 2. Mecanismo

- `Type=notify`, `NotifyAccess=main`, `WatchdogSec=1200`, `Restart=on-failure` (sin cambios).
- `systemd_notify.py`: `sd_notify` con la biblioteca estándar (`socket` AF_UNIX/DGRAM). Respeta
  `NOTIFY_SOCKET` (incluidas direcciones abstractas `@`). Sin esa variable es un **no-op**; un
  fallo de envío se registra una sola vez y **nunca** propaga excepciones. Los mensajes son tres
  constantes (`READY=1`, `WATCHDOG=1`, `STOPPING=1`): sin datos dinámicos, sin secretos.
- **`WATCHDOG=1` se envía en un único punto:** `Collector._loop`, justo después de que
  `run_cycle()` vuelva sin excepción y de registrar «ciclo completado». No hay ningún temporizador
  independiente: si el bucle se cuelga o el ciclo falla, no hay latido.
- Un ciclo con 0 mercados, 0 observaciones o 0 precios **sí** envía el latido.
- Un ciclo fallido no lo envía; los reintentos, el backoff y el log siguen igual. Con fallo
  persistente: sin latido → pasan 1200 s → systemd mata el proceso (SIGABRT) → `Restart=on-failure`.

### Limitación conocida
La señal prueba que el bucle completa ciclos, **no** que escriba. Un ciclo con 0 mercados no toca
la base de datos (ni siquiera `ensure_partitions`), así que con el catálogo vacío el watchdog no
detectaría un fallo de partición hasta que reaparezcan mercados. Lo cubre `collector health`
(§5). Tampoco detecta que *todos* los mercados de un ciclo sean rechazados por PostgreSQL
(`failed_markets`): el ciclo se completa y cada rechazo se registra con `log.error`; es el comportamiento de
aislamiento del 2026-09-30.

## 3. READY=1

**Sí**, una vez: dentro de `run()`, tras abrir la conexión a PostgreSQL y tomar el bloqueo
singleton, justo antes de entrar en el bucle.

- Configuración cargada: `Settings` ya existe y las credenciales se validan al primer ciclo.
- PostgreSQL accesible: la conexión se acaba de abrir y el advisory lock se ha ejecutado.
- **No** espera a Betfair: la sesión es perezosa. Esperar al primer ciclo con `Type=notify`
  convertiría una caída temporal de Betfair en un fallo de arranque (`TimeoutStartSec`) y
  gastaría el `StartLimit`. Para eso está el watchdog, que empieza a contar tras `READY=1`.
- Si otro collector tiene el bloqueo, el proceso sale con error antes de `READY=1`.

`STOPPING=1` se envía al entrar en el `finally` de `run()`. systemd desactiva el watchdog durante
`systemctl stop/restart`, de modo que una parada manual normal nunca cuenta como fallo.

## 4. Por qué 1200 s

> Revisión del mismo día: el valor inicial era 900 s. Con 3 fallos consecutivos el hueco entre
> latidos llega a ~1080 s en el peor caso (ciclo de recuperación incluido), de modo que 900 s podía
> reiniciar el servicio por una secuencia recuperable. Se sube a 1200 s.

Peor hueco entre dos latidos con los valores por defecto (intervalo 60 s, `LONG_WAIT` 300 s,
timeout de red 30 s por ciclo fallido, 270 s para el ciclo sano final):

| Situación | Hueco máximo |
|---|---|
| Ciclo sano | ≈ 330 s |
| 2 fallos transitorios seguidos | ≈ 750 s |
| 3 fallos seguidos | ≈ 1080 s |
| 4 fallos seguidos | ≈ 1410 s |
| 5 fallos seguidos (fallo persistente) | ≈ 1740 s |

1200 s tolera hasta 3 fallos transitorios seguidos y expira con 4 o más, es decir, un fallo
persistente reinicia el servicio en ≈ 20–25 min. Reiniciar es inocuo (sesión nueva) y
`RestartSec=60` más el `StartLimit` impiden un bucle de reinicios. Los tests protegen
`WatchdogSec` > hueco de 3 fallos y < hueco de 5. **Limitación:** `collector_interval_seconds`
admite hasta 3.600 s; con un intervalo superior a ~600 s habría que subir `WatchdogSec`.

## 5. `collector health` (solo lectura)

Nuevos avisos, sin escribir nada:
- existe la partición del mes UTC actual y la del siguiente, en `market_observation` y
  `runner_price`;
- sus límites son exactamente `[mes 00:00 UTC, mes siguiente 00:00 UTC)` (DEC-019), comparados con
  `partition_bounds()` de la migración 005;
- filas en `market_observation_default` **y** `runner_price_default` (antes solo la primera).

## 6. Decisión: backfill del 1–5 de octubre

**DECISIÓN = NO REALIZAR.** Los datos de C2 tienen otra resolución (snapshots del catálogo cada
30 min, sin libro de precios por hito) y no reconstruyen exactamente el dataset operacional.
Mezclarlos introduciría observaciones de otra naturaleza en la fuente de verdad. El hueco
(30-sep 23:40 UTC → 5-oct 13:17 UTC) queda documentado como tal.

**Propuesta para `DECISIONS.md` (no añadida):** una decisión metodológica corta («no se rellenan
huecos del collector con datos de otra fuente o resolución; se documentan»). Se propone porque
condiciona el análisis, pero se deja a criterio del responsable. DEC-002 no se toca.

## 7. Validación

- Tests nuevos: `tests/test_collector_watchdog.py` (24). Total **570** (197 críticos, 64 de
  integración), `ruff check` y `ruff format --check` limpios, `git diff --check` limpio.
- `systemd-analyze verify deploy/edgecourt-collector.service`: sin avisos.
- **No se provocó ningún fallo real en producción**: la expiración se valida por tests.

## 8. Validación en producción (2026-10-05)

Unidad instalada por el responsable y servicio reiniciado el **2026-10-05 13:39:36 UTC**
(15:39:36 CEST, `ActiveEnterTimestamp`).

| Comprobación | Resultado |
|---|---|
| `Type` | `notify` |
| `NotifyAccess` | `main` |
| `WatchdogUSec` | `20min` |
| `Restart` | `on-failure` |
| Estado | `active (running)`, `NRestarts=0` |
| `READY=1` | aceptado por systemd (un `Type=notify` sin `READY` no llega a `active`) |
| Parada anterior | limpia: SIGTERM, bloqueo liberado, sesión Betfair cerrada, «collector detenido» (13:39:35 UTC) |
| Nuevo proceso | adquiere el advisory lock y hace login en Betfair |
| Primer ciclo tras el reinicio | completado correctamente |
| `collector health` | OK (última observación con precios hace ~1 min) |
| keepAlive y persistencia | siguen funcionando |

**No se provocó deliberadamente la expiración real del watchdog en producción**: está cubierta por
los tests (`test_watchdog_expires_on_persistent_failure` y `test_failed_cycle_sends_no_watchdog`).
La decisión de **no realizar backfill del 1–5 de octubre** sigue vigente (§6).

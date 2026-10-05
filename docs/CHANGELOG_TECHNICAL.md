# Changelog técnico

Histórico **humano** de los hitos relevantes: qué cambió, por qué, qué resultado dio y cómo se
verificó. No sustituye a `git log`. Solo recoge lo que una sesión futura necesita saber.
Los más recientes, arriba.

Las entradas anteriores al 2026-09-27 se han reconstruido a partir de los mensajes de commit.

---

## 2026-10-05 — Incidente: particiones con límites en Europe/Madrid (collector sin persistir 109,6 h)

- **Cambio:**
  - Migración `005_utc_monthly_partitions.sql`:
    - funciones `utc_month_bounds` y `partition_bounds`;
    - `ensure_month_partition` con límites UTC explícitos (`+00`);
    - **reparación transaccional** del esquema: preserva las filas atrapadas en `default`, reajusta
      septiembre a UTC y las reinserta, con verificación por recuento y md5 y `ROLLBACK` si algo
      no cuadra;
    - mes actual y siguiente (UTC).
  - `repositories.ensure_partitions`: mes en UTC y **mes siguiente** siempre garantizado.
  - `collector health`: instantes en UTC real terminados en `Z` (antes, hora local etiquetada UTC).
  - [DEC-019](DECISIONS.md#dec-019): particionado mensual en UTC explícito.
- **Motivo:** las particiones se crearon con fechas sin zona, interpretadas en Europe/Madrid:
  septiembre terminó a las 22:00 UTC del 30-sep. El 1-oct a las 00:00 UTC crear octubre solapó con
  32 observaciones (y 64 precios) atrapadas en `default` y **cada ciclo falló**: 1.311 ciclos
  fallidos y **109,6 h sin persistir** (del 30-sep 23:40:39 UTC al 5-oct 13:17:01 UTC). `systemd` lo
  veía `active` y nadie vigilaba `collector health`.
- **Resultado:**
  - Producción, 13:12 UTC: **ANTES = DESPUÉS**: 3.462 observaciones y 4.973 precios, mismo md5;
    `default` de 32/64 filas a 0/0; ids conservados (3450–3481).
  - Ensayo previo con una copia real de producción en la base de test: mismo resultado.
  - El collector se recuperó **sin reiniciar** (primer ciclo 13:16:01 UTC; primera observación
    nueva 13:17:01 UTC, id 3482, en `market_observation_202610`).
  - Pérdida estimada de ~1.700–2.300 observaciones y unos 600 hitos; **no recuperable
    exactamente**, parcialmente desde C2. **Sin backfill.**
- **Tests:** +31 (**546** en total, **192** críticos, **63** de integración): reproducción exacta
  del incidente y de la reparación, 5 zonas de sesión, cambio de mes, DST, cambio de año,
  reversión ante fallo e idempotencia. `ruff` limpio.
- **Commit:** pendiente.
- **Docs:** [incidente](audits/2026-10-partition-timezone-incident.md),
  [POSTGRESQL](architecture/POSTGRESQL.md), [DEC-019](DECISIONS.md#dec-019).

## 2026-10-05 — Revisión de 3.5-C2: `.es` ofrece ATP, de forma parcial

- **Cambio:** ninguno en código. Revisión de solo lectura de los artefactos de C2 (313 slots).
- **Resultado:**
  - Cobertura del 100 % y 155 mercados de tenis.
  - **ATP Pekín: 3 mercados** (2 individuales y 1 de dobles); **ATP Shanghai: 44**; ATP Tokyo: 0;
    Challenger: 0; WTA Beijing: 108.
  - Conclusión intermedia: **`.es` ofrece ATP, pero de forma parcial**. Supera el resultado B del
    30-sep. C2 sigue abierto hasta el 19-oct; Shanghái es la prueba independiente.
  - Esta revisión descubrió el incidente de particiones.
- **Commit:** pendiente, junto con la entrada anterior.
- **Docs:** [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md).

## 2026-09-30 — Incidente: desbordamiento de `max_spread_pct` y aislamiento por mercado

- **Cambio:**
  - Migración `004_widen_max_spread_pct.sql`: `market_observation.max_spread_pct` pasa de
    `numeric(8,4)` a `numeric(12,4)`, sin clamp.
  - `collector.run_cycle`: un mercado rechazado por PostgreSQL por sus **datos** (`DataError`,
    `IntegrityError`) solo deshace su propia transacción. El ciclo continúa con los demás y lo
    registra en `failed_markets`. Los errores de conexión siguen abortando el ciclo.
- **Motivo:** el 29-sep, entre las 02:15 y las 03:41 UTC, el mercado `1.263050661` (spread del
  peor runner de 13.471–17.331 %) desbordó la columna en su hito de 24 h. La excepción salía del
  bucle y abortaba el ciclo entero: **19 ciclos fallidos**, backoff y pérdida de observaciones de
  otros mercados.
- **Resultado:**
  - Migración aplicada en producción a las 05:32 UTC; datos existentes intactos (suma de control
    idéntica).
  - Collector reiniciado de forma controlada a las 05:35 UTC; 0 desbordamientos desde entonces.
  - Pérdida estimada: 1 hito de 24 h y entre 16 y ~48 observaciones adaptive. Recuperable solo
    parcialmente desde C2. **Sin backfill.**
- **Tests:** +12, de ellos 6 de integración contra PostgreSQL con los valores reales del incidente
  y la cota máxima de 98.909,9010 %. Suite: **515 passed** (174 críticos, 41 de integración).
  `ruff` limpio.
- **Commit:** `0096db2`.
- **Docs:** [incidente](audits/2026-09-spread-overflow-incident.md),
  [POSTGRESQL](architecture/POSTGRESQL.md).

## 2026-09-30 — Auditoría intermedia de 3.5-C2 y cierre de 3.5-A

- **Cambio:** ninguno en código. Revisión de solo lectura de los artefactos de C2, del journal y de
  la base de datos.
- **Resultado:**
  - **C2 RUNNING:** 58/58 slots (cobertura del 100 %), integridad verificada.
  - **WTA Beijing 2026 visible** en `.es` (31 `MATCH_ODDS`); ATP Beijing, ATP Tokyo y Challenger
    no visibles; 0 mercados «audit=sí, collector=no». La conclusión ATP sigue abierta.
  - **3.5-A DONE:** unas 69 h con 0 `INVALID_SESSION_INFORMATION`, 268 keepAlive y 0 fallos de
    autenticación.
- **Commit:** `0096db2` (junto con la entrada anterior).
- **Docs:** [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md).

## 2026-09-27 — Phase 3.5-C2: herramienta de auditoría del catálogo de tenis

- **Cambio:**
  - `market/catalogue_audit.py` y `market/catalogue_report.py`.
  - Comandos `edgecourt betfair catalogue-audit` y `catalogue-report`.
  - `deploy/edgecourt-catalogue-audit.service` y `.timer`, y `scripts/install_catalogue_audit.sh`.
  - `MarketFilter` admite `market_type_codes=None`; el payload del collector no cambia.
- **Motivo:** observar de forma reproducible si el catálogo `.es` ofrece ATP 500/1000 (Pekín,
  Tokio, Shanghái), sin tocar el collector.
- **Diseño:**
  - proceso corto de solo lectura cada 30 min, sin PostgreSQL;
  - un artefacto por slot, conservando el primer snapshot válido;
  - escritura atómica;
  - catálogo direccionado por contenido y libros por slot con hash;
  - informe con métricas del collector y de la Semana 1, y cruce de solo lectura con
    `betfair_market`.
- **Resultado:** ejecución real de prueba en un directorio temporal (7 mercados `.es`,
  `market_data_delayed = true`, sin secretos, idempotencia comprobada). Timer **INSTALLED** por el
  responsable del proyecto el 2026-09-27; hasta la ventana, las ejecuciones terminan como
  `outside_window` sin login. Experimento **NOT STARTED** (del 2026-09-29 00:00 UTC al
  2026-10-19 00:00 UTC).
- **Tests:** +31 en `tests/test_catalogue_audit.py`. Suite: **503 passed** (165 críticos).
  Barrera de solo lectura en verde. `ruff` limpio.
- **Commit:** `eb3949f`.
- **Docs:** [protocolo](investigations/2026-10-spanish-exchange-atp-catalogue.md),
  [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md).

## 2026-09-27 — Phase 3.5-A: keepAlive preventivo de la sesión española

- **Cambio** (`src/edgecourt/market/auth.py`):
  - intervalo de keepAlive por jurisdicción, **15 min para `es`/`it`** (el resto sigue en 1 h);
  - caducidad documentada de 20 min en una constante con cita oficial;
  - una respuesta de keepAlive que no es JSON se trata como fallo controlado;
  - log `INFO` al renovar la sesión, sin token.
- **Motivo:** la sesión `.es` caduca a los 20 min (documentación oficial) y el keepAlive estaba a
  1 h. Resultado: `INVALID_SESSION_INFORMATION` y reautenticación cada ~20 min (~72 al día).
- **Resultado:**
  - Betfair acepta el keepAlive y extiende la sesión: una sonda confirmó la sesión válida a los
    21 y 24 min sin reautenticar.
  - Servicio reiniciado de forma controlada a las 07:59:59 UTC; keepAlive real a las 08:15:02 y
    0 caducidades en los 22 min siguientes.
  - La reautenticación se mantiene como segunda barrera.
- **Tests:** +15 (14 en `test_betfair_keepalive.py` y 1 en `test_betfair_collector.py`). Suite:
  **472 passed** (151 críticos). `ruff` limpio. Barrera de solo lectura en verde.
- **Commit:** `6751047` (implementación) y `02189b2` (cierre documental en VALIDATING). Estado: **DONE** el 2026-09-30, con la validación de 24 h superada.
- **Docs:** [validación](audits/2026-09-session-keepalive-validation.md),
  [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md),
  [BETFAIR_COLLECTOR](architecture/BETFAIR_COLLECTOR.md).

## 2026-09-27 — Investigación 3.5-C: catálogo ATP/WTA de Betfair

- **Cambio:** ninguno en código, datos ni configuración. Investigación de solo lectura:
  - auditoría del código de descubrimiento;
  - calendarios oficiales ATP y WTA;
  - snapshot directo de la API (operaciones `list*` y `getDeveloperAppKeys`);
  - comparación con las webs públicas betfair.es y betfair.com.
- **Motivo:** explicar por qué la muestra no contuvo torneos ATP ni WTA regulares.
- **Resultado:**
  - Conclusión F: calendario en parte + restricción del catálogo de la cuenta `.es`, con
    evidencia empírica fuerte y sin confirmación oficial.
  - Ningún bug en EdgeCourt.
  - Clave Delayed verificada.
  - Sesión de 20 min en `.es` confirmada oficialmente.
  - `DEC-002` entra en revisión.
- **Tests:** no proceden. Collector `active`, `collector health` OK.
- **Commit:** `54f2e94`.
- **Docs:** [investigación](investigations/2026-09-atp-wta-catalogue.md),
  [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md).

## 2026-09-27 — Memoria persistente del proyecto

- **Cambio:**
  - Estructura de documentación en `docs/`: `PROJECT_STATUS`, `ROADMAP`, `DECISIONS`, este
    changelog, `phases/`, `audits/` y `architecture/`.
  - `CLAUDE.md` mínimo en la raíz.
  - Enlaces de estado en `README.md` e `IMPLEMENTATION_PLAN.md`.
- **Motivo:** no depender del historial de conversación para reconstruir el proyecto, y
  conservar las auditorías y las decisiones con su porqué.
- **Resultado:**
  - 18 decisiones registradas.
  - Roadmap reconciliado con la numeración del plan.
  - Documentado `POSTGRESQL.md`, que la migración 001 citaba como `PERSISTENCE.md` sin que
    existiera.
- **Tests:** no proceden, porque solo cambia documentación. Suite recolectada sin cambios: 457
  tests (143 críticos, 35 de integración).
- **Commit:** `dab0233`.
- **Docs:** [`README.md`](README.md) (convenciones).

## 2026-09-27 — Auditoría Semana 1 (2.ª parte) y apertura de Phase 3.5

- **Cambio:** ninguno en código ni en datos. Análisis de solo lectura de 1.497 observaciones, 62
  mercados y 7 días de logs.
- **Motivo:** medir la calidad real del mercado, no solo la presencia de liquidez.
- **Resultado:**
  - Collector con 100 % de uptime.
  - Mercado de muy baja calidad: spread del favorito con mediana del 13,2 % y 16 ticks.
  - `close` no fiable en 45 de 58 casos.
  - Sesión que caduca cada 20 min.
  - Ningún torneo ATP ni WTA regular en la muestra.
  - Se abre Phase 3.5 y se bloquea Phase 4 ([DEC-015](DECISIONS.md#dec-015)).
- **Tests:** no proceden.
- **Commit:** `dab0233` (junto con la entrada anterior).
- **Docs:** [auditoría](audits/2026-09-week1-market-audit.md) y
  [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md).

## 2026-09-18 16:00 — El collector entra en operación desatendida

- **Cambio:** servicio `edgecourt-collector` activo tras reiniciar la máquina, con el run
  `4e74757f`.
- **Resultado:** funciona sin interrupciones hasta la fecha de la auditoría. Hubo 2 fallos de DNS
  justo tras los arranques, recuperados en ~2 min.
- **Docs:** [BETFAIR_COLLECTOR](architecture/BETFAIR_COLLECTOR.md).

## 2026-09-18 — Servicio systemd del collector

- **Cambio:**
  - Unidad endurecida.
  - Advisory lock para garantizar un único collector.
  - `collector health`.
  - Logs JSON al journal.
  - `StartLimit*` movidos a `[Unit]`; en `[Service]` se ignoraban en silencio.
  - `UMask=0077`.
- **Motivo:** operación desatendida y supervisable.
- **Tests:** 457 (143 críticos, 35 de integración).
- **Commits:** `9bfebca`, `c292cbd`.
- **Docs:** [DEC-008](DECISIONS.md#dec-008).

## 2026-09-18 — PostgreSQL como fuente de verdad operativa

- **Cambio:**
  - Esquema en las migraciones 001–003: catálogo, serie temporal particionada, ledger inmutable
    y guarda de retención.
  - El collector escribe solo en PostgreSQL. Parquet pasa a ser exportación.
  - Captura híbrida integrada.
- **Motivo:** el patrón de escritura 24/7 (pequeña, concurrente, idempotente) no encaja con
  Parquet.
- **Resultado:** verificado contra Betfair y PostgreSQL reales. Exportación: 18 observaciones en
  la base y 18 en el fichero.
- **Incidentes corregidos en `7eea268`:**
  - DSN de tests expuesta en un traceback. Pendiente verificar que la contraseña se rotó.
  - Clave reservada en `extra=` del logging.
  - `BEGIN`/`COMMIT` dentro de las migraciones.
  - Tests de integración omitidos en silencio.
- **Tests:** 449 (137 críticos, 30 de integración).
- **Commits:** `025bab2`, `7eea268`, `34ac7a6`.
- **Docs:** [POSTGRESQL](architecture/POSTGRESQL.md), [DEC-004](DECISIONS.md#dec-004),
  [DEC-006](DECISIONS.md#dec-006), [DEC-007](DECISIONS.md#dec-007),
  [DEC-014](DECISIONS.md#dec-014), [DEC-016](DECISIONS.md#dec-016).

## 2026-09-18 — Acceso real a Betfair (jurisdicción `.es`)

- **Cambio:**
  - `edgecourt betfair check`: verificación aislada que no escribe nada.
  - `BETFAIR_JURISDICTION` para los endpoints de identidad; corrige
    `AUTHORIZED_ONLY_FOR_DOMAIN_ES`.
- **Resultado:** login y lectura verificados contra la cuenta real. La Betting API global
  funciona con sesión `.es`.
- **Tests:** 358 (97 críticos).
- **Commits:** `dbd7a9f`, `46fae68`.

## 2026-09-18 — PHASE 4 (plan): regresión logística

- **Cambio:** LogReg antisimétrica (sin intercepto, imputación a 0, escalado sin centrar). La `C`
  se elige solo en VALIDATION.
- **Resultado:**
  - Brier skill frente al Elo: +4,71 % en VAL y +3,60 % en TEST.
  - ECE en TEST: de 0,053 a 0,0135.
  - Es más conservadora que el Elo.
- **Tests:** 334 (91 críticos).
- **Commit:** `fa8f11e`.
- **Docs:** [MODELS](MODELS.md).

## 2026-09-18 — PHASE 8 (plan): collector de Betfair, solo lectura

- **Cambio:** cliente `httpx` con 3 operaciones; barrera de 4 comprobaciones contra la capacidad
  de apostar; snapshots con timestamp real.
- **Motivo:** se adelanta por R4, porque la muestra del exchange solo se acumula hacia delante.
- **Tests:** 299 (73 críticos).
- **Commit:** `ba92168`.
- **Docs:** [DEC-009](DECISIONS.md#dec-009), [BETFAIR_SETUP](BETFAIR_SETUP.md).

## 2026-09-18 — PHASES 1–3 (plan): dataset, Elo y features

- **PHASE 1** (`ed7cb77`):
  - 113.544 partidos ATP.
  - 100 % de acuerdo con el mirror.
  - `match_id` por hash de contenido, que recupera 479 partidos.
  - 130 tests.
- **PHASE 2** (`c3fae0a`):
  - Benchmark Elo.
  - Hallazgo de sobreconfianza (R13).
  - Split en código y registro de las evaluaciones sobre TEST.
  - 192 tests.
- **PHASE 3** (`6439bcb`):
  - 21 features sin leakage, con tests de envenenamiento y control positivo.
  - 219 tests.
- **Docs:** [DATA](DATA.md) y [MODELS](MODELS.md).

## 2026-09-17 — PHASE 0 (plan): bootstrap

- **Cambio:** configuración tipada con `BETTING_MODE` = `paper`, logging con redacción de
  secretos, almacenamiento Parquet + DuckDB y CLI.
- **Tests:** 48 (13 críticos).
- **Commit:** `d8f0885`.

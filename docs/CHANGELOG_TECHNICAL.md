# Changelog técnico

Histórico **humano** de los hitos relevantes: qué cambió, por qué, qué resultado dio y cómo se
verificó. No sustituye a `git log`. Solo recoge lo que una sesión futura necesita saber.
Los más recientes, arriba.

Las entradas anteriores al 2026-09-27 se han reconstruido a partir de los mensajes de commit.

---

## 2026-11-04 — Carril A, A3: predicciones y promoción manual de modelos

- **Cambio:** paquete `prediction/` (servicio puro), `models/promotion.py`, cuatro funciones de
  repositorio nuevas (`production_model`, `promote_model`, `markets_for_prediction`,
  `insert_prediction`) y los comandos `edgecourt model promote <id> --by <responsable> [--apply]` y
  `edgecourt predict [--apply]`. Ambos solo informan por defecto. `predict` deja de ser un
  comando reservado. Sin migración ni cambios en el collector.
- **Diseño:** el modelo `production` es el que está registrado en `model_version` (DEC-013); la
  promoción verifica el SHA-256, copia a `models/production/` y retira el anterior en la misma
  transacción. `predict` no predice con datos viejos (umbral 14 días), exige `player_id` ya
  rellenado y coincidente con la resolución, y cuenta cada mercado excluido con su motivo.
  Escribe en `prediction` de forma idempotente. El modelo no ve precios.
- **Verificación:** `ruff` limpio y recolección de tests correcta. Tests nuevos
  (`tests/test_prediction.py` 26 y `tests/test_model_promotion.py` 4) **escritos y no ejecutados**:
  diferidos a `/validar-todo`. No se ha tocado la base operativa.
- **Pendiente:** el responsable decide qué challenger promover (los dos existentes son del 18-sep,
  entrenados hasta 2022) y ejecuta `identity resolve --apply` antes del primer `predict --apply`.

## 2026-11-03 — Carril A, A2: identidad de jugadores y superficie del torneo

- **Cambio:** paquete nuevo `identity/` (`players.py`, `tournaments.py`, `service.py`), comando
  `edgecourt identity resolve [--apply]` y dos funciones de repositorio (`runners_for_identity`,
  `set_runner_player_id`, que solo rellena vacíos). Sin migración ni cambios en el collector.
- **Resultado (informe, sin escribir):** 156 mercados ATP individuales listos y 312 de 312 runners
  resueltos, tras confirmar el responsable el alias «Manuel Cerundolo» = C0C8 (antes: 151 y 307).
- **Verificación:** `ruff` limpio. `tests/test_identity.py` (20) **pasa** (20 passed). La suite
  completa queda diferida a `/validar-todo`. La base operativa no se ha modificado.
- **Pendiente:** ejecutar `uv run edgecourt identity resolve --apply` (lo hace el responsable; la
  escritura fue denegada por permisos desde la sesión), commit y `/validar-todo`.

## 2026-11-03 — Carril A, A1: datos al día y features «a fecha de hoy»

- **Cambio:**
  - Dataset de partidos refrescado: 114.132 partidos (+588), hasta el 2026-11-01.
  - `features/builder.py` y `features/elo.py`: extracción de la emisión y la actualización de estado
    en funciones reutilizables (la tabla por lotes sale **idéntica**, verificado en 114.132 filas).
  - Módulo nuevo `features/asof.py`: features de un partido futuro con el estado final; rechaza
    fechas anteriores al último partido (fuga temporal).
  - Comando `edgecourt data freshness`.
- **Verificación:** 0 diferencias frente al lote en 23 features × 3 partidos reales. Tests nuevos
  escritos (12) **no ejecutados**: diferidos a `/validar-todo`. `ruff` limpio.
- **Limitación:** ranking, puntos, edad y altura de un partido futuro son los del último partido de
  cada jugador, no el ranking semanal actual.
- **Pendiente:** commit, `/validar-todo`, y A2 (identidad de jugadores). Modelo sin reentrenar.
- **Docs:** [plan del carril A §11](phases/PHASE_A_PAPER_LANE_PLAN.md).

## 2026-11-03 — Estrategia v1 del carril A congelada (provisional)

- **Cambio:** solo documentación. [`PHASE_A_STRATEGY_V1_PROPOSAL.md`](phases/PHASE_A_STRATEGY_V1_PROPOSAL.md)
  pasa a **congelada** (SHA-256 `32fa7eda9958d76b9a77335aa80ba407a0a78f40ec547ec337ee8306d07b6b63`). Valores: Shin sobre BACK; decisión única entre 20 y 60 min
  antes del inicio; EV neto ≥ +3 % y edge ≥ 3 pp (anomalía si > 15 pp); stake plano del 1 %.
- **Supuestos sin verificar:** comisión del 5 % (por defecto del proyecto), stake mínimo de la
  cuenta y historial mínimo de un jugador. Se corrigen como supuestos, no como cambio de estrategia.
- **Cambios futuros:** solo como v2, a partir de los estudios del carril B; nunca con resultados paper.

## 2026-11-03 — DEC-022: dos carriles, estudio y paper operativo

- **Cambio:** solo documentación. Se acepta [DEC-022](DECISIONS.md#dec-022), que **sustituye
  parcialmente a DEC-015** (conservada como histórico) y define el carril A (paper mínimo, ATP
  individual prematch) en paralelo al carril B (estudio). Nuevo
  [plan del carril A](phases/PHASE_A_PAPER_LANE_PLAN.md) con pasos A0–A7.
- **Motivo:** el responsable quiere ver resultados con dinero ficticio sin esperar a que termine el
  estudio. DEC-001 (solo paper) no cambia; el dinero real exigiría otra DEC.
- **Pendiente:** fijar la estrategia v1 (A0). Nada de código todavía.

## 2026-11-03 — 3.5-B, Pasos 0 y 1: precio de cierre (DEC-021)

- **Cambio:**
  - [DEC-021](DECISIONS.md#dec-021): definición del cierre (última observación pre-play con precios
    anterior a la primera señal de juego; fiable si ≤ 5 min de la referencia de inicio).
  - Migración `006_market_closing_observation.sql`: solo crea la vista `market_closing_observation`.
  - `repositories.closing_observations` / `closing_summary` (solo lectura).
  - 13 tests de integración nuevos. Sin cambios en el collector, la cadencia ni los datos.
- **Paso 0 (diagnóstico):** de 288 mercados terminados con precios, 233 (80,9 %) tienen el cierre a
  ≤ 5 min del inicio publicado. Los 55 restantes: libro vacío antes del inicio (23), el catálogo
  deja de listarlos antes (27, causa **no verificada**) y aplazados (5).
- **Paso 2 aparcado por el responsable (2026-11-03):** no se toca el collector; se mide con la vista sobre datos nuevos. Ver el plan §11.
- **Consulta puntual a la API sobre C1 (autorizada, solo lectura):** no concluyente. La API solo conserva los libros de mercados cerrados 1–2 días (7 de 33 devueltos) y los C1 comprobados se jugaron con normalidad; no expone cuándo pasaron a juego. Ver el plan §10.
- **Migración 006 aplicada en producción el 2026-11-03** (esquema 5 → 6; la vista reproduce la línea base: 233 fiables de 289 con cierre en la cohorte desde el 5-oct). **Paso 2 sin autorizar.**
- **Docs:** [plan](phases/PHASE_03_5_B_CLOSING_PRICE_PLAN.md).

## 2026-11-03 — DEC-020: objetivo ATP prematch sobre `.es`, declarado parcial; 3.5-B desbloqueada

- **Cambio:** solo documentación. Se acepta [DEC-020](DECISIONS.md#dec-020), que **sustituye a
  DEC-002** (conservada como histórico). Se actualizan `PROJECT_STATUS.md`, `ROADMAP.md` y el
  documento de la fase.
- **Motivo:** C2 mostró que `.es` ofrece ATP de forma muy desigual (Shanghái 132 individuales, Pekín
  3, Tokio 0). El objetivo se mantiene, con la fuente declarada parcial y los resultados reportados
  por torneo.
- **Siguiente:** 3.5-B pasa a prioridad. Plan previo y definición del closing price en
  `DECISIONS.md` **antes** de tocar el collector.
- **Commit:** `docs: accept DEC-020 ...` (ver `git log`).

## 2026-11-03 — Cierre de 3.5-C2: el catálogo `.es` ofrece ATP, de forma muy desigual

- **Cambio:** ninguno en código. Revisión final de solo lectura de la ventana completa
  (29-sep → 19-oct) y sección *Resultados* en la investigación (el protocolo no se modifica).
  Informe versionado en `docs/investigations/2026-10-spanish-exchange-atp-catalogue/` con
  `SHA256SUMS` del conjunto bruto.
- **Resultado:**
  - Cobertura **960/960 (100 %)** en las tres ventanas; validez cumplida.
  - 407 mercados: **ATP Shanghai 161** (132 individuales), **ATP Beijing 4** (3 individuales),
    ATP Tokyo 0, Challenger 0, WTA Beijing 132, WTA Wuhan 110.
  - **Resultado A con matices.** Calidad en la última hora (individuales): BACK y LAY ≈ 97 %,
    spread del favorito P50 3,6–4,2 %.
  - Los 85 mercados que no están en `betfair_market` empezaron antes de la recuperación del
    collector (5-oct 13:17 UTC): sin problema de descubrimiento.
- **Calendarios oficiales:** ATP Pekín confirmado (se jugó 30-sep → 6-oct). **NO VERIFICADO:** Tokio, Shanghái y WTA (requisito de validez del protocolo), causa de la
  oferta desigual, naturaleza de los 24 mercados de Shanghái del 5-oct. DEC-002 se sustituye después, en DEC-020.
- **Estado del collector (misma revisión):** `active` desde el 1-nov, ~41.000 ciclos y 11 fallidos
  (DNS puntual) desde el 5-oct, watchdog sin actuar.
- **Timer de C2 desactivado** por el responsable el mismo día (verificado `disabled` / `inactive`).
- **Docs:** [Resultados](investigations/2026-10-spanish-exchange-atp-catalogue.md#resultados),
  [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md).

## 2026-10-05 — Hardening: watchdog de systemd y salud de particiones

- **Cambio:**
  - `src/edgecourt/systemd_notify.py`: `sd_notify` con la biblioteca estándar; no-op sin `NOTIFY_SOCKET`.
  - `Collector`: `READY=1` tras abrir PostgreSQL y tomar el bloqueo; `WATCHDOG=1` solo tras un
    ciclo completado sin excepción (también con 0 mercados); `STOPPING=1` al parar.
  - `deploy/edgecourt-collector.service`: `Type=notify`, `NotifyAccess=main`, `WatchdogSec=1200`.
  - `collector health`: partición del mes actual y siguiente, límites UTC y filas en `default` de
    `market_observation` y `runner_price` (solo lectura).
- **Motivo:** `systemd` veía `active` un collector con 1.311 ciclos fallidos seguidos.
- **Resultado:** 24 tests nuevos (**570** en total, 197 críticos, 64 de integración); `ruff` limpio;
  `systemd-analyze verify` sin avisos. **Validado en producción el 2026-10-05 13:39:36 UTC**:
  `Type=notify`, `NotifyAccess=main`, `WatchdogUSec=20min`, `Restart=on-failure`, READY aceptado,
  parada anterior limpia (bloqueo liberado), primer ciclo correcto y health OK. No se provocó una
  expiración real (cubierta por tests).
- **Decisión registrada:** backfill del 1–5 de octubre = **NO REALIZAR** (los datos de C2 tienen
  otra resolución y no reconstruyen el dataset operacional). No añadida a `DECISIONS.md`: propuesta
  en la auditoría.
- **Commit:** `feat: add systemd watchdog for collector`. Corrección de particiones: `4ca87d0`.
- **Docs:** [auditoría](audits/2026-10-collector-watchdog.md).

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
- **Commit:** `4ca87d0`.
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

# Changelog técnico

Histórico **humano** de los hitos relevantes: qué cambió, por qué, qué resultado dio y cómo se
verificó. No sustituye a `git log`. Solo recoge lo que una sesión futura necesita saber.
Los más recientes, arriba.

Las entradas anteriores al 2026-09-27 se han reconstruido a partir de los mensajes de commit.

---

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
- **Commit:** pendiente.
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

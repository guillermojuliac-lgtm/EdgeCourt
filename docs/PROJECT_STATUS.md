# Estado del proyecto

> **Estado ACTUAL, no un histórico.** Se reescribe al cerrar cada tarea relevante.
> Última actualización: **2026-11-03**.
> Histórico: [`CHANGELOG_TECHNICAL.md`](CHANGELOG_TECHNICAL.md) · convenciones:
> [`README.md`](README.md).

## Objetivo

Responder con datos a una sola pregunta:

> ¿Podemos generar probabilidades de tenis suficientemente buenas como para identificar de
> manera consistente situaciones donde el precio de Betfair ofrece esperanza matemática positiva
> **después de costes**?

El proyecto intenta **refutar** esa hipótesis. El criterio de éxito está fijado de antemano
([DEC-003](DECISIONS.md#dec-003), [`METRICS.md`](METRICS.md)).

## Modo de operación

- **Solo PAPER.** `BETTING_MODE` solo admite `paper` y **no existe código capaz de apostar**.
  Las apuestas reales están **prohibidas** ([DEC-001](DECISIONS.md#dec-001)).
- **Betfair es de solo lectura:** `listEvents`, `listMarketCatalogue` y `listMarketBook`, con
  tests que lo garantizan.
- **Ningún modelo se promueve automáticamente** ([DEC-013](DECISIONS.md#dec-013)).

## Fase actual

**Phase 3.5 — Market validation**, en curso →
[`phases/PHASE_03_5_MARKET_VALIDATION.md`](phases/PHASE_03_5_MARKET_VALIDATION.md).

**Phase 4 y siguientes, bloqueadas** hasta cerrar 3.5 ([DEC-015](DECISIONS.md#dec-015)).
Roadmap completo en [`ROADMAP.md`](ROADMAP.md).

| Phase | Estado |
|---|---|
| 0 Bootstrap · 1 Datos · 2 Elo · 3 Features | ✅ |
| 8 Collector Betfair | ✅ en operación |
| **3.5 Market validation** | 🔶 en curso |
| 4 Probability models | ◐ LogReg hecha · XGBoost bloqueado |
| 5 Calibration/Value · 6 Risk/Paper · 7 Evaluation · 8b Identity | ⏳ / 🚫 |

## Stack

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.13 gestionado por `uv` ([DEC-017](DECISIONS.md#dec-017)) |
| Datos y ML | pandas, numpy, pyarrow, DuckDB y scikit-learn. XGBoost todavía no está instalado |
| Operativo | PostgreSQL 18 nativo, con `psycopg` 3 |
| Betfair | `httpx` directo, login por certificado, jurisdicción `es` ([DEC-009](DECISIONS.md#dec-009)) |
| Configuración | `pydantic-settings` y `.env` (nunca versionado) |
| Operación | systemd (`edgecourt-collector.service`) |
| Calidad | pytest (570 tests: 197 críticos y 64 de integración), ruff |
| Ausentes | Redis y Docker (ver DECISIONS, «Hechos verificados sin decisión documentada») |

## Arquitectura

```
TennisMyLife ─► match_facts (Parquet) ─► Elo ─► features ─► LogReg (challenger)
                                                                  │  (calibración, Value, Risk y Paper: sin implementar)
Betfair (solo lectura) ─► collector (systemd) ─► PostgreSQL ─► export-parquet
```

- **PostgreSQL** es la fuente de verdad operativa: catálogo, observaciones y precios. Las
  predicciones y el ledger vivirán ahí ([DEC-004](DECISIONS.md#dec-004),
  [`architecture/POSTGRESQL.md`](architecture/POSTGRESQL.md)).
- **Parquet** es el formato analítico e histórico: el dataset de tenis, y Betfair solo por
  exportación ([DEC-005](DECISIONS.md#dec-005)).
- **Fuentes de datos:** TennisMyLife (primaria, uso no comercial) + mirror de Sackmann (contraste)
  ([`DATA.md`](DATA.md)). Betfair Exchange para las cuotas en vivo. **No hay histórico de
  cuotas:** la validación económica solo puede ser hacia delante (R4).
- Principios de separación modelo / Value / Risk / Ledger: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Collector

- **Estado:** en operación desde el 2026-09-18.
  - Reinicios controlados el 2026-09-27 (keepAlive de 3.5-A) y el 2026-09-30 a las 05:35 UTC
    (aislamiento de fallos por mercado).
  - Del 29-sep a las 02:15 al 29-sep a las 03:41 UTC hubo 19 ciclos fallidos por el
    [desbordamiento de `max_spread_pct`](audits/2026-09-spread-overflow-incident.md), ya
    corregido (migración 004).
  - **Del 30-sep a las 23:40 UTC al 5-oct a las 13:17 UTC (109,6 h) no persistió nada** por el
    [incidente de particiones en hora de Madrid](audits/2026-10-partition-timezone-incident.md)
    (1.311 ciclos fallidos). Corregido el 2026-10-05 con la migración 005 y la política UTC
    ([DEC-019](DECISIONS.md#dec-019)). Se recuperó sin reiniciar y el 5-oct se hizo el reinicio
    controlado que cargó el código nuevo.
  - **Watchdog de systemd** (`Type=notify`, `WatchdogSec=1200`): **VALIDADO en producción**
    el 2026-10-05 (`WatchdogUSec=20min`, READY aceptado, parada limpia, health OK)
    ([auditoría](audits/2026-10-collector-watchdog.md)). Un fallo persistente de ciclos acaba en
    reinicio por systemd; la expiración real no se provocó en producción (la cubren los tests).
  - **Backfill del 1–5 de octubre: decisión = NO REALIZAR** (C2 tiene otra resolución).
  - **Estado al 2026-11-03:** `active` desde el 2026-11-01 16:04 CET, `NRestarts=0`,
    `collector health` OK (17.507 observaciones, 686 en 24 h, 668 con precios; 477 mercados).
    Del 5-oct al 3-nov: ~41.000 ciclos completados y **11 fallidos**, todos el mismo error aislado
    (`Temporary failure in name resolution` al contactar con el login de Betfair), recuperados en
    el ciclo siguiente. El watchdog no ha tenido que actuar. Los reinicios del 31-oct y 1-nov
    fueron paradas por SIGTERM (limpias).
  - Detalle en [`architecture/BETFAIR_COLLECTOR.md`](architecture/BETFAIR_COLLECTOR.md).
- **Sesión:** keepAlive preventivo cada 15 min (sesión `.es` de 20 min). Verificado en producción
  ([validación](audits/2026-09-session-keepalive-validation.md)).
- **Captura:** híbrida, con hitos y cadencia adaptive ([DEC-006](DECISIONS.md#dec-006)). Las
  observaciones sin precio se guardan igualmente ([DEC-007](DECISIONS.md#dec-007)).
- **`MINIMUM_LIQUIDITY` = 50**, sin cambios ([DEC-012](DECISIONS.md#dec-012)).
- **Retención de 90 días:** diseñada, **no activada**. Hay 0 archivos y ningún comando de purga
  ([DEC-014](DECISIONS.md#dec-014)).

## Métricas importantes actuales

| Área | Métrica | Valor | Fuente |
|---|---|---|---|
| Dataset | partidos ATP | 113.544 (1990–2026) | [DATA](DATA.md) |
| Modelo | LogReg, TEST 2024–25 | Brier 0,21422 · ECE 0,0135 · skill frente al Elo +3,60 % | [MODELS](MODELS.md) |
| Modelo | Elo, TEST | ECE ≈ 0,053: sobreconfiado, no apto para el Value sin calibrar | [MODELS](MODELS.md) |
| Collector | últimos 7 días | 10.054 ciclos, 0 fallos, P50 60,13 s | [auditoría](audits/2026-09-week1-market-audit.md) |
| Mercado | muestra | 1.497 obs · 62 mercados · 60 partidos · 26 adaptive (92,1 % de las obs) | ídem |
| Mercado | mercados con precio alguna vez | **43,5 %** | ídem |
| Mercado | spread del favorito (mediana) | **13,2 % · 16 ticks**; ninguno a ≤3 ticks | ídem |
| Mercado | `top_depth` (mediana) | **~67 €** | ídem |
| Mercado | `close` a > 5 min del último inicio publicado | **45 / 58** | ídem |
| Mercado | competiciones en la muestra | Davis Cup, BJK Cup y Laver Cup. **Ningún torneo ATP ni WTA regular** | ídem |
| Catálogo | exchange visible para la cuenta `.es` (27-sep, 07:22 UTC) | 2 disciplinas (Soccer 2.995, Tennis **3**). Tenis = solo Laver Cup. betfair.com mostraba además ATP 250, WTA 500/250 y Challengers | [investigación 3.5-C](investigations/2026-09-atp-wta-catalogue.md) |
| Datos | Application Key | **Delayed** (`delayData = true`); libros con `isMarketDataDelayed = true` (retraso de 1–180 s) | ídem |
| **C2 (final, 3-nov)** | catálogo `.es` del 29-sep al 19-oct | **960/960 slots (100 %)**, 407 mercados. **ATP Shanghai 161 (132 individuales)**; **ATP Beijing 4 (3 individuales)**; ATP Tokyo 0; Challenger 0; WTA Beijing 132; WTA Wuhan 110. **Resultado A con matices:** `.es` ofrece ATP, de forma muy desigual | [Resultados](investigations/2026-10-spanish-exchange-atp-catalogue.md#resultados) |
| C2 | calidad en torneos regulares (individuales, última hora) | BACK y LAY 97 % (Shanghái); spread del favorito P50 3,6–4,2 %; `top_depth` P50 ~21 €. Delayed Key | ídem |
| Collector | observaciones perdidas por el incidente de particiones | ~1.700–2.300 y unos 600 hitos (estimación). **No recuperables exactamente**; parcialmente desde C2. Sin backfill | [incidente](audits/2026-10-partition-timezone-incident.md) |

## Próximos pasos

1. **Decisión del responsable del proyecto sobre la fuente de mercado y el alcance realista.**
   La investigación 3.5-C (completada) indica que el catálogo `.es` no ofreció ATP regular en la
   muestra.
2. **Phase 3.5-C2: DONE** (ventana 29-sep → 19-oct completada; revisión final del 2026-11-03).
   - Cobertura 960/960 (100 %). **Resultado A con matices:** Shanghái (Masters 1000) 132
     individuales; Pekín 3; Tokio 0; Challenger 0.
   - **Calendarios oficiales:** ATP Pekín **confirmado** (30-sep → 6-oct, final Djokovic v De
     Minaur); Tokio, Shanghái y WTA **NO VERIFICADO** (faltan las URLs correctas de atptour.com
     y un calendario WTA legible).
   - **Timer desactivado el 2026-11-03** (`disabled` / `inactive`, verificado). La unidad
     `.service` queda instalada.
   - [Protocolo y resultados](investigations/2026-10-spanish-exchange-atp-catalogue.md).
   - El collector sigue recogiendo en paralelo (3.5-D).
3. **Phase 3.5-A: DONE** (2026-09-30).
   - Más de 24 h superadas: unas 69 h revisadas.
   - 0 `INVALID_SESSION_INFORMATION`, 268 keepAlive correctos y 0 fallos de autenticación.
4. **Phase 3.5-B: en curso** ([DEC-020](DECISIONS.md#dec-020), [DEC-021](DECISIONS.md#dec-021)).
   - Pasos 0 y 1 **hechos**: definición del cierre aprobada, vista `market_closing_observation`
     (migración 006) y tests. **Pendiente: aplicar la migración 006 en producción.**
   - Línea base: 80,9 % de los mercados con precios tienen el cierre a ≤ 5 min del inicio
     publicado (233 de 288, mercados terminados). Causas de los demás en el
     [plan](phases/PHASE_03_5_B_CLOSING_PRICE_PLAN.md) §8.
   - **Paso 2** (tocar el collector para ver la transición a juego): **sin autorizar**; como
     mucho mejoraría el 9,4 % de casos cuya causa no está verificada.

## Decisiones abiertas

- Definición de closing price (3.5-B), ya prioritaria.
- Método de desvigado y convención de signo del CLV (fase de Value/CLV).
- Uno o dos procesos de larga duración (D9).
- **Resueltas:** fuente de mercado y alcance ([DEC-020](DECISIONS.md#dec-020), que sustituye a
  DEC-002, aceptada el 2026-11-03: ATP individual prematch sobre `.es`, declarado parcial y
  dependiente del torneo), monitorización (watchdog, validado) y backfill 1–5-oct (NO REALIZAR).
- Propuesta no registrada: entrada en `DECISIONS.md` sobre no rellenar huecos del collector con
  datos de otra fuente o resolución.

## Problemas conocidos

**Datos y operación**
- `close` no es un precio de cierre fiable, porque `market_start_time` se retrasa: 45 de 58
  casos. Crítico para el CLV (3.5-B).
- Calidad de mercado muy baja y muestra atípica. **No** se han convertido las cifras en reglas
  todavía.
- El collector captura también WTA y dobles, que el modelo no predice: solo 33 de 62 mercados
  eran individuales masculinos.
- `betfair_runner.player_id` está vacío: Phase 8b pendiente.
- **Catálogo `.es` restringido** (investigación 3.5-C y C2). En la primera semana la API no devolvió
  ATP ni WTA regular. En C2 sí aparece ATP, pero **muy desigual** (Shanghái 161 mercados, Pekín 4,
  Tokio 0, sin Challenger). EdgeCourt no pierde mercados: tras el 5-oct no falta ninguno. La
  restricción viene de la fuente y su causa no está verificada.
- **Datos retrasados** (Delayed Key, 1–180 s). Con esta clave, `totalMatched` está disponible
  **por mercado** y **no por selección**, según la tabla oficial *Delay & Live Application Keys
  Overview*; los datos son coherentes con ello. Además, la Live Key no admite uso de solo lectura,
  según Betfair.
- **Corregido el 2026-09-30:** `max_spread_pct numeric(8,4)` desbordaba con spreads por encima
  del 10.000 % y un mercado inválido abortaba el ciclo entero. Solución: migración 004
  (`numeric(12,4)`) y aislamiento por mercado. Se perdieron entre 16 y ~48 observaciones
  adaptive y 1 hito de 24 h; **no se ha hecho backfill**
  ([incidente](audits/2026-09-spread-overflow-incident.md)).
- **Corregido el 2026-10-05:** particiones con límites en Europe/Madrid. El collector estuvo
  109,6 h sin persistir (**1.311 ciclos fallidos**) hasta aplicar la migración 005 y la política
  UTC ([DEC-019](DECISIONS.md#dec-019)). Datos existentes íntegros (ANTES = DESPUÉS); pérdida
  estimada de ~1.700–2.300 observaciones, **no recuperable exactamente**, parcial desde C2; **sin
  backfill** ([incidente](audits/2026-10-partition-timezone-incident.md)).
- **Corregido el 2026-10-05:** `collector health` etiquetaba como «UTC» la hora local; ahora
  muestra UTC real terminado en `Z`.
- **Mitigado el 2026-10-05:** watchdog de systemd (`WatchdogSec=1200`) ligado a los ciclos
  completados ([auditoría](audits/2026-10-collector-watchdog.md)). **Límite:** prueba que el bucle
  completa ciclos, no que persista (con 0 mercados no se toca la base); `collector health`
  sigue sin ejecutarse automáticamente.
- **Residual:** errores puntuales de DNS al contactar con Betfair (11 en 29 días), que se
  recuperan solos.

**Seguridad**
- El commit `7eea268` indica que la contraseña de la base de tests quedó expuesta en un
  traceback y **debe rotarse**. **No se ha verificado** que se haya rotado.

**Documentación desalineada** (detectada el 2026-09-27; no corregida para no ampliar el alcance):
- `README.md`, secciones *Datos* y *Snapshots*: siguen describiendo Parquet como almacén canónico
  de todo y una deduplicación por `market_id:selection_id:label`. Ya no es así
  ([DEC-004](DECISIONS.md#dec-004)).
- `docs/ARCHITECTURE.md`:
  - la cabecera dice «Estado: PHASE 0»;
  - describe el ledger como JSONL, sustituido por [DEC-016](DECISIONS.md#dec-016);
  - marca «Operación 24/7» como pendiente, cuando el collector ya está en systemd.
- `docs/MODELS.md`: la cabecera dice «Estado: PHASE 0».
- `deploy/README.md` y `docs/BETFAIR_SETUP.md` (*Despliegue como servicio*): describen un usuario
  `edgecourt`, `/opt/edgecourt` y `/etc/edgecourt/edgecourt.env`. La unidad real corre con el
  usuario del proyecto, desde el repositorio, y lee el `.env` del proyecto
  ([DEC-008](DECISIONS.md#dec-008)).
- `migrations/001_initial_schema.sql` cita `docs/PERSISTENCE.md`, que no existe. Ahora lo cubre
  [`architecture/POSTGRESQL.md`](architecture/POSTGRESQL.md). La migración no se edita.
- `IMPLEMENTATION_PLAN.md`: la línea de estado de la cabecera es de antes de la Phase 3.5 (se ha
  añadido un aviso que remite aquí).

**Documentos pendientes de reconciliación.** Los documentos desalineados de la lista anterior
**no se corrigen todavía** (decisión del 2026-09-27). Hasta reconciliarlos, la fuente del estado
actual es este documento.

## NO VERIFICADO

Se mantienen explícitamente como no verificados hasta que haya evidencia:

| Punto | Estado |
|---|---|
| Rotación de la contraseña de la base de tests expuesta (commit `7eea268`) | **No verificado** |
| Motivo histórico de no usar Redis ni Docker | **No verificado.** Solo consta la ausencia; ningún documento explica el porqué |
| «ATP prematch» como objetivo inicial | **No verificado como decisión explícita.** Estaba originalmente implícita en el código ([DEC-002](DECISIONS.md#dec-002)) |
| Que la documentación oficial de Betfair reconozca un catálogo `.es` reducido | **No verificado.** La restricción se ha observado empíricamente, pero ninguna página oficial consultada la afirma |
| Cómo es el catálogo `.es` en otras semanas (Grand Slams, Masters) | **No verificado.** Solo hay 9 días de histórico y un snapshot |

Resueltos el 2026-09-27 (investigación 3.5-C):
- **Tipo de Application Key: verificado, Delayed.**
- **Causa de la ausencia de ATP/WTA regulares:** combinación de calendario, en parte, y
  restricción del catálogo `.es`, **con evidencia empírica fuerte pero sin confirmación oficial**.

**No conservado**
- Los resultados de la **primera parte** de la Auditoría Semana 1 no están en el repositorio.

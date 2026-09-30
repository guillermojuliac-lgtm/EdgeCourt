# Estado del proyecto

> **Estado ACTUAL, no un histórico.** Se reescribe al cerrar cada tarea relevante.
> Última actualización: **2026-09-30**.
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
| Calidad | pytest (515 tests: 174 críticos y 41 de integración), ruff |
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
| C2 (intermedio) | catálogo `.es` del 29-sep 00:00 al 30-sep 05:00 UTC | 58/58 slots (100 %). **WTA Beijing 2026 visible** (31 `MATCH_ODDS`); **ATP Beijing, ATP Tokyo y Challenger no visibles**. No es una conclusión | [Phase 3.5](phases/PHASE_03_5_MARKET_VALIDATION.md) |

## Próximos pasos

1. **Decisión del responsable del proyecto sobre la fuente de mercado y el alcance realista.**
   La investigación 3.5-C (completada) indica que el catálogo `.es` no ofreció ATP regular en la
   muestra.
2. **Phase 3.5-C2:** observación del catálogo `.es` durante ATP 500 (Pekín/Tokio, desde el
   30-sep) y Masters 1000 (Shanghái, desde el 7-oct).
   - Experimento **RUNNING** desde el 2026-09-29 00:00 UTC; termina el 2026-10-19 00:00 UTC.
   - Auditoría intermedia del 30-sep: cobertura del 100 %; WTA Beijing visible; ATP Beijing,
     Tokyo y Challenger no visibles. **La conclusión ATP sigue abierta**; Shanghái (desde el
     7-oct) es una prueba independiente.
   - [Protocolo](investigations/2026-10-spanish-exchange-atp-catalogue.md).
   - El collector sigue recogiendo en paralelo (3.5-D).
3. **Phase 3.5-A: DONE** (2026-09-30).
   - Más de 24 h superadas: unas 69 h revisadas.
   - 0 `INVALID_SESSION_INFORMATION`, 268 keepAlive correctos y 0 fallos de autenticación.
4. **Phase 3.5-B:** closing price, **con la prioridad supeditada al punto 1**.

## Decisiones abiertas

- **Fuente de mercado y alcance realista.** Con la cuenta `.es`, ¿es viable el objetivo ATP
  prematch ([DEC-002](DECISIONS.md#dec-002))? Es la decisión que condiciona todo lo demás.
- Definición de closing price (3.5-B).
- Método de desvigado y convención de signo del CLV (fase de Value/CLV).
- Uno o dos procesos de larga duración (D9).

## Problemas conocidos

**Datos y operación**
- `close` no es un precio de cierre fiable, porque `market_start_time` se retrasa: 45 de 58
  casos. Crítico para el CLV (3.5-B).
- Calidad de mercado muy baja y muestra atípica. **No** se han convertido las cifras en reglas
  todavía.
- El collector captura también WTA y dobles, que el modelo no predice: solo 33 de 62 mercados
  eran individuales masculinos.
- `betfair_runner.player_id` está vacío: Phase 8b pendiente.
- **Catálogo `.es` restringido** (investigación 3.5-C). En 9 días la API nunca devolvió ATP ni WTA
  regular, ni Challengers. EdgeCourt no pierde mercados: la restricción viene de la fuente.
- **Datos retrasados** (Delayed Key, 1–180 s). Con esta clave, `totalMatched` está disponible
  **por mercado** y **no por selección**, según la tabla oficial *Delay & Live Application Keys
  Overview*; los datos son coherentes con ello. Además, la Live Key no admite uso de solo lectura,
  según Betfair.
- **Corregido el 2026-09-30:** `max_spread_pct numeric(8,4)` desbordaba con spreads por encima
  del 10.000 % y un mercado inválido abortaba el ciclo entero. Solución: migración 004
  (`numeric(12,4)`) y aislamiento por mercado. Se perdieron entre 16 y ~48 observaciones
  adaptive y 1 hito de 24 h; **no se ha hecho backfill**
  ([incidente](audits/2026-09-spread-overflow-incident.md)).
- `edgecourt collector health` etiqueta como «UTC» la hora local. Defecto de presentación, sin
  corregir.

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

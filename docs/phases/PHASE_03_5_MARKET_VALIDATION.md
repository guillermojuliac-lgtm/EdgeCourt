# Phase 3.5 — Market validation / calidad del mercado Betfair

| Campo | Valor |
|---|---|
| Estado | **EN CURSO** (abierta el 2026-09-27) |
| Bloquea | Phase 4 en adelante: XGBoost, calibración, Value Engine y CLV (ver [DEC-015](../DECISIONS.md#dec-015)) |
| Evidencia de partida | [Auditoría Semana 1](../audits/2026-09-week1-market-audit.md) |
| Numeración | Fase añadida en el [ROADMAP](../ROADMAP.md); no existe en `IMPLEMENTATION_PLAN.md` |

## Objetivo

Determinar si el mercado de Betfair al que accede EdgeCourt tiene **calidad suficiente**, y
corregir los problemas que **impedirían evaluar correctamente un modelo**, antes de apoyar el
Value Engine o el CLV en estos datos.

## Por qué existe esta fase

La auditoría de la Semana 1 mostró que el pipeline de captura funciona (100 % de uptime), pero
que los datos que captura todavía no sirven para la pregunta del proyecto:

- **`close` no es un precio de cierre:** 45 de 58 capturas `close` estaban a más de 5 min del
  último inicio publicado, porque `market_start_time` cambió en 38 de 62 mercados.
- **La calidad del mercado es muy baja:** spread del favorito con mediana del 13,2 % (16 ticks),
  `top_depth` con mediana de ~67 €, y solo el 43,5 % de los mercados mostró algún precio.
- **La muestra es atípica:** solo Davis Cup, Billie Jean King Cup y Laver Cup, sin torneos ATP/WTA
  regulares. Solo 33 de 62 mercados son individuales masculinos.
- **La sesión caduca cada ~20 min** y el `keepAlive` está a 1 h.

Construir un Value Engine o medir CLV sobre esa base produciría resultados que no se podrían
distinguir de artefactos de captura.

---

## Líneas de trabajo

### 3.5-A — Revisar el keepAlive de la sesión

**Problema.**
- Aparece `INVALID_SESSION_INFORMATION` cada 20,05 ± 0,007 min: 503 eventos en 7 días.
- `KEEP_ALIVE_INTERVAL = timedelta(hours=1)` en `src/edgecourt/market/auth.py`, así que el
  `keepAlive` nunca llega antes de la caducidad.
- La recuperación funciona (503 de 503 ciclos completados), pero cada caducidad cuesta una
  llamada fallida y un login.

**Tareas.**
1. ~~Confirmar en la documentación oficial de Betfair la duración de la sesión para la
   jurisdicción de la cuenta.~~ **Hecho el 2026-09-27:** «The session expiry time is currently
   20 minutes on the Italian & Spanish Exchange» (*Login & Session Management*, citado en la
   [investigación 3.5-C](../investigations/2026-09-atp-wta-catalogue.md) §10). Coincide con lo
   observado.
2. ~~Decidir el intervalo de `keepAlive`.~~ **Hecho:** 15 min para `es` e `it`
   (`keep_alive_interval()` en `auth.py`); el resto de jurisdicciones sigue en 1 h.
3. ~~Test de regresión.~~ **Hecho:** 14 tests en `tests/test_betfair_keepalive.py` y 1 en
   `tests/test_betfair_collector.py`. Suite: 472 passed (151 críticos).
4. **Verificado en producción el 2026-09-27:**
   - servicio reiniciado a las 07:59:59 UTC;
   - keepAlive real aceptado a las 08:15:02;
   - 0 `INVALID_SESSION_INFORMATION` en los 22 min siguientes.

   Una sonda independiente confirmó que la sesión sigue válida a los minutos 21 y 24 sin
   reautenticar.

> **Estado (2026-09-27): implementado y verificado.** Solo queda el seguimiento de 24 h del
> criterio de «hecho». Detalle en la
> [validación](../audits/2026-09-session-keepalive-validation.md).

**Hecho cuando:** 24 h sin reautenticaciones forzadas, o con una causa distinta documentada.
*Pendiente de cumplirse:* 24 h desde el 2026-09-27 08:00 UTC.

**Riesgo:** bajo. Es un cambio acotado en `auth.py`.

### 3.5-B — Rediseñar la definición de `close` para un CLV fiable

**Problema.**
- Los hitos se capturan **una vez**, contra la hora de inicio que se conoce en ese momento. Si
  Betfair retrasa `market_start_time` después, el hito no se repite.
- Resultado: el `close` puede estar 11–150 min antes del inicio real.
- Además, cada retraso vuelve a meter al mercado en la ventana de 0–10 min, lo que multiplica
  las observaciones adaptive (~37 por mercado en lugar de ~10).

**Preguntas a responder antes de implementar:**
1. ¿Qué es el «precio de cierre» en EdgeCourt? Candidatas:
   - la última observación pre-play antes de que el mercado pase a `inplay`, o a
     `SUSPENDED`/`CLOSED` en la transición;
   - la última observación dentro de N min del inicio *final*.

   La definición debe ser **única y fijada antes de ver resultados** (ver `docs/METRICS.md`,
   apartado CLV).
2. ¿Se puede derivar retrospectivamente de las observaciones adaptive ya guardadas? En los 26
   mercados adaptive probablemente sí, en parte. En los demás, no.
3. ¿Hace falta detectar la transición a `inplay`? Hoy el collector deja de planificar cuando
   `minutes_left < 0` según la hora publicada, así que puede no ver nunca esa transición.
4. ¿Cómo registrar la hora de inicio vigente en cada observación?
   `observed_at + minutes_to_start` ya la reconstruye; hay que valorar si hace falta una columna
   explícita.
5. ¿Qué hacer con el hito `close` histórico? Conservarlo tal cual (es un dato honesto de lo que
   se capturó) y marcar en la documentación de análisis que no es un cierre.

**Restricciones.**
- No reescribir las observaciones existentes: el timestamp real y la etiqueta original son
  datos.
- Cualquier cambio de esquema va en una migración nueva (`004_…`) y se registra en
  `DECISIONS.md`.
- Revisar si el tope teórico de observaciones por mercado (`estimate_daily_observations`) sigue
  siendo válido.

**Hecho cuando:**
- existe una definición de closing price documentada, implementada y con tests;
- sobre datos nuevos, el porcentaje de cierres a más de 5 min del inicio real es medible y bajo.

**Riesgo:** medio. Toca la planificación del collector y quizá el esquema.

### 3.5-C — Investigar la ausencia de torneos ATP/WTA regulares

> **Estado (2026-09-27): investigación completada; decisión pendiente.**
> Resultado completo en [`investigations/2026-09-atp-wta-catalogue.md`](../investigations/2026-09-atp-wta-catalogue.md).
>
> **Conclusión F) combinación.**
> - **A) calendario, en parte:** la semana del 14-sep no tuvo ATP Tour.
> - **D) efecto de jurisdicción, con evidencia empírica fuerte:** la API con sesión `.es`
>   devuelve el mismo catálogo reducido que la web pública betfair.es (el 27-sep, solo Laver Cup:
>   3 mercados; en todo el exchange, solo Soccer y Tennis). Mientras tanto, betfair.com mostraba
>   ATP Chengdu y Hangzhou, WTA Singapur y Seúl, Challengers y WTA 125.
> - **B) descartado:** EdgeCourt no filtra ni pierde mercados. La Delayed Key (verificada) no
>   explica la restricción.
>
> La documentación oficial de Betfair consultada **no** confirma un catálogo `.es` reducido. Falta
> saber cómo es ese catálogo en otras semanas.
>
> La tabla de hipótesis de abajo es la planificación original y se conserva como registro.

**Observación.**
- En 9 días (18–27 sep 2026) el catálogo solo contuvo Davis Cup (33 mercados), Billie Jean King
  Cup (20) y Laver Cup (9).
- Días enteros sin mercados: el 21-sep hubo 0; el 22 y el 23, solo 3 cada día.
- En la ventana de 7 días, la mediana de mercados visibles por ciclo fue 0.

**No asumir la causa.** Hipótesis a contrastar, cada una con su evidencia:

| # | Hipótesis | Evidencia actual | Cómo contrastarla |
|---|---|---|---|
| 1 | **Calendario:** semana sin torneos regulares o con pocos | Desconocida. La semana de la Davis Cup y la Laver Cup puede tener un calendario ATP reducido, pero **no se ha verificado** | Consultar el calendario ATP/WTA de esas fechas; seguir observando (3.5-D) |
| 2 | **Filtro del collector** | El filtro de código es `eventTypeIds=["2"]` (tenis), `marketTypeCodes=["MATCH_ODDS"]`, ventana `now → now + 26 h` y `maxResults = 200`, **sin** filtro de competición. El máximo observado fueron 9 mercados por ciclo, lejos del límite | Leer una vez el catálogo sin ventana temporal, o con una más amplia, en solo lectura, y comparar |
| 3 | **Catálogo de la API** (qué devuelve `listMarketCatalogue` para esta cuenta) | La Betting API usada es `api.betfair.com`; el commit `46fae68` verificó empíricamente que funciona con una sesión `.es`, pero **no** que devuelva el mismo catálogo | Comparar `listEventTypes` / `listCompetitions` con lo visible en la web para la misma cuenta |
| 4 | **Configuración** (tipo de Application Key: *delayed* o *live*) | **No verificado** qué clave está en uso. La documentación recomienda empezar con la *delayed* | Comprobarlo en el portal de desarrolladores, sin exponer la clave |
| 5 | **Jurisdicción española** (`BETFAIR_JURISDICTION = es`, confirmada en los logs: `"jurisdiction": "es"`) | Solo es una hipótesis. **No hay evidencia** todavía de que el catálogo o la liquidez `.es` estén segregados | Documentación oficial de Betfair sobre el exchange español; comparación con la web |
| 6 | Otra causa | — | — |

**Entregable:** `docs/investigations/2026-09-atp-wta-catalogue.md` (o similar), con la
conclusión y la evidencia de cada hipótesis. Si la causa es estructural y no tiene solución, se
registra una decisión sobre la viabilidad del objetivo inicial (ver
[DEC-002](../DECISIONS.md#dec-002)).

**Restricción:** toda consulta extra a la API debe ser de solo lectura y puntual, con el mismo
cliente y las mismas garantías (ver `docs/BETFAIR_SETUP.md`, apartado *Garantía de solo lectura*).

### 3.5-D — Seguir recopilando datos

El collector sigue en marcha, sin cambios de cadencia, `MINIMUM_LIQUIDITY` ni esquema, mientras
se trabaja en A–C. Cada semana de muestra es irrecuperable (riesgo R4 de
`IMPLEMENTATION_PLAN.md`).

**Tareas:**
- Supervisión periódica: `edgecourt collector health`, journal y conteos por día.
- Nueva auditoría cuando haya muestra con torneos regulares, o a las ~4 semanas. Irá en un
  fichero **nuevo** (`docs/audits/2026-10-…`) que compare con la de la Semana 1, sin
  sobrescribirla.

---

## Criterio de salida

Phase 3.5 se cierra cuando se cumplan **todas** estas condiciones:

1. **3.5-A** resuelto y verificado en producción.
2. **3.5-B:** existe una definición fiable de closing price, documentada en `DECISIONS.md`,
   implementada y con tests. Su fiabilidad está medida sobre datos nuevos.
3. **3.5-C:** se conoce, con evidencia, por qué faltaron los torneos regulares, y existe una
   decisión sobre qué mercados son el objetivo realista.
4. **3.5-D:** hay una auditoría posterior con muestra suficiente para describir la calidad del
   mercado en torneos regulares, o para confirmar que no están disponibles.

En resumen: comprender suficientemente la disponibilidad y la calidad del mercado, y tener una
definición fiable de closing price, **antes** de basar Value o CLV en estos datos.

## Lo que esta fase NO hace

- No cambia `MINIMUM_LIQUIDITY` ([DEC-012](../DECISIONS.md#dec-012)).
- No convierte las cifras de calidad de la Semana 1 en filtros permanentes.
- No entrena modelos ni empieza XGBoost, calibración ni el Value Engine.

## Registro

| Fecha | Evento | Referencia |
|---|---|---|
| 2026-09-27 | Auditoría Semana 1 (2.ª parte) completada; se abre la fase | [auditoría](../audits/2026-09-week1-market-audit.md) |
| 2026-09-27 | Estructura de memoria persistente del proyecto (`docs/`) | [CHANGELOG_TECHNICAL](../CHANGELOG_TECHNICAL.md) |
| 2026-09-27 | 3.5-C investigada: conclusión F (calendario en parte + jurisdicción `.es` con evidencia empírica). Duración de sesión de 20 min confirmada oficialmente (3.5-A). Clave Delayed verificada | [investigación](../investigations/2026-09-atp-wta-catalogue.md) |
| 2026-09-27 | 3.5-A implementada: keepAlive cada 15 min en `es`/`it`, manejo de respuestas no JSON y log de renovación. Servicio reiniciado; keepAlive real aceptado a las 08:15:02 UTC, sin caducidades | [validación](../audits/2026-09-session-keepalive-validation.md) |

## Pendiente de decisión (surgida de 3.5-C)

- **Fuente de mercado y alcance realista.** ¿Se acepta el catálogo `.es`? ¿Se observa primero su
  evolución longitudinal en semanas ATP 500/1000? ¿Se consideran otras vías legales? Lo decide el
  responsable del proyecto.
- **Prioridad de 3.5-B.** Depende de lo anterior. Un closing price sobre `.es`, con datos
  retrasados de 1–180 s y liquidez muy baja, sería como mucho orientativo.

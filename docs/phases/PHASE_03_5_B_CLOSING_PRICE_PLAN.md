# Plan de 3.5-B — Precio de cierre (closing price)

> **Estado (2026-11-03): definición APROBADA ([DEC-021](../DECISIONS.md#dec-021)); Pasos 0 y 1
> HECHOS (migración 006 aplicada en producción el 2026-11-03); Paso 2 SIN AUTORIZAR.** Se desbloquea
> por [DEC-020](../DECISIONS.md#dec-020). Marco de la fase:
> [`PHASE_03_5_MARKET_VALIDATION.md`](PHASE_03_5_MARKET_VALIDATION.md) §3.5-B.
> Antes de tocar el collector, la definición de la §3 debe pasar a `DECISIONS.md` como DEC propia.

## 1. Evidencia medida (solo lectura sobre PostgreSQL)

Cohorte: observaciones desde el **2026-10-05 13:17 UTC** (tras la recuperación del collector):
**342 mercados, 14.051 observaciones** (41,1 por mercado). «Inicio final» = `market_start_time`
vigente en `betfair_market`; el inicio publicado en cada observación se reconstruye como
`observed_at + minutes_to_start`.

| Hecho | Valor |
|---|---|
| Mercados cuyo inicio publicado cambió al menos una vez | **272 de 342 (80 %)**: 268 retrasos y 4 adelantos |
| Desplazamiento del inicio (primer → último valor publicado) | P50 150 min · P90 1.660 min · máx. 3.140 min |
| Mercados con precios alguna vez | 306 (36 sin precios nunca: no tienen cierre posible) |
| Última observación pre-play **con precios**, distancia al inicio final | ≤ 2 min: 221 (72 %) · ≤ 5 min: **234 (76 %)** · ≤ 10 min: 258 (84 %) · P50 0,7 min · P90 57 min |
| Esa observación, por tramo | ≤ 5 min: 234 · 5–30: 32 · 30–120: 15 · > 120: 25 |
| Mercados con `inplay = true` observado | **solo 3** (en 2 de ellos, 25,0 y 27,4 min **antes** del inicio publicado; en el tercero, 0,3 min) |
| Etiqueta de esa última observación pre-play | `adaptive` en la gran mayoría; `close` solo en 26 de 342 |
| Observaciones por mercado frente al tope teórico (`estimate_daily_observations`) | **41,1 frente a 35** (6 hitos + 29 adaptive) |

Lecturas, con su grado de certeza:
- **Observado:** la etiqueta `close` no sirve como cierre; el cierre efectivo lo da la cadencia
  `adaptive`. El inicio publicado es inestable (80 % de los mercados), y el juego puede empezar
  **antes** del inicio publicado.
- **Observado:** el collector casi nunca ve la transición a juego (3 de 342). **Según el código**
  (no medido): al cumplirse la hora publicada, el mercado sale del catálogo consultado
  (`market_start_from = now`) y la planificación se detiene (`minutes_left < 0`).
- **NO VERIFICADO:** por qué 72 de los 306 mercados con precios (los tramos > 5 min) acaban con la
  última observación lejos del inicio final. Solo en 18 de ellos el inicio cambió después de la
  última observación, y en los de 5–30 min el catálogo los vio poco después. Hipótesis a
  contrastar (no afirmadas): salida anticipada del catálogo al empezar el juego antes de la hora
  publicada, aplazamientos o retiradas.
- Los precios llegan con 1–180 s de retraso (Delayed Key): el cierre tiene un ruido intrínseco de
  hasta unos 3 min, que debe tenerse en cuenta al fijar la tolerancia.

## 2. Respuestas a las cinco preguntas de la fase

1. **¿Qué es el cierre?** Ver la definición propuesta en la §3 (basada en la transición a juego, no
   en la hora publicada).
2. **¿Se puede derivar retrospectivamente?** **Sí, sin escribir nada**, con las columnas que ya se
   guardan (`market_status`, `inplay`, `has_prices`, `observed_at`, `minutes_to_start`). Vale para
   toda la historia; la calidad del cierre depende de cada mercado (columna de fiabilidad).
3. **¿Hace falta detectar la transición a juego?** Es lo que haría el cierre robusto, pero hoy
   apenas se observa (3 de 342). Es la única parte que requiere tocar el collector (Paso 2,
   condicionado).
4. **¿Columna con la hora de inicio vigente?** **No hace falta.** `observed_at + minutes_to_start`
   la reconstruye (así se midió el 80 % de cambios). Evita una migración de datos.
5. **¿Qué se hace con el hito `close` histórico?** Se conserva sin tocar (es un dato honesto de lo
   capturado) y se documenta que **no es un cierre**.

## 3. Definición propuesta (borrador de la futura DEC-021)

> **Cierre de un mercado** = la observación con mayor `observed_at` entre las que cumplen
> `market_status = 'OPEN'`, `inplay = false` y `has_prices`, y cuyo `observed_at` es anterior a
> la primera observación del mercado con `inplay = true` o `market_status` distinto de `OPEN`
> (si existe).
>
> **Fiabilidad.** `close_gap_min` = minutos entre esa observación y la referencia de inicio:
> la primera observación `inplay = true` si existe; si no, el último inicio publicado. El cierre
> es **fiable si `close_gap_min ≤ 5`**; si no, se marca **no fiable** y se informa su número, sin
> descartarlo en silencio ni sustituirlo.
>
> Los mercados sin ninguna observación con precios **no tienen cierre**.

- Una sola definición, fijada **antes** de ver resultados de CLV (`METRICS.md`).
- El umbral de 5 min es el de la propia fase («cierres a más de 5 min del inicio real»); es
  compatible con el retraso de la Delayed Key (hasta ~3 min).
- Limitación declarada: sin transición a juego observada, el «inicio real» es el último publicado,
  que se sabe inestable.
- No decide el desvigado ni el signo del CLV (siguen abiertos para la fase de Value/CLV).

## 4. Implementación propuesta, en dos pasos

### Paso 1 — Definición e implementación de solo lectura (sin tocar el collector)
- **Qué:** vista SQL `market_closing_observation` (una fila por mercado: observación de cierre,
  `close_gap_min`, `close_reliable`, motivo si no hay cierre) y una función Python fina para
  consultarla.
- **Esquema:** una migración **nueva** `006_market_closing_observation.sql` que solo crea la vista.
  No se editan migraciones aplicadas ni se reescribe ninguna observación. Alternativa sin
  migración: consulta en Python (menos reutilizable; no recomendada).
- **Tests:** casos sintéticos (juego antes del inicio publicado, retraso, aplazamiento, mercado sin
  precios, `SUSPENDED`/`CLOSED`, empate de `observed_at`) e integración sobre la base de test.
- **Medición:** informe de línea base sobre la historia y sobre datos nuevos, con la tabla de la
  §1 recalculada con esta definición y el desglose de los no fiables.
- **Riesgo:** bajo. Producción: aplicar la migración con `edgecourt db migrate`, por el
  responsable. No requiere reiniciar el collector.

### Paso 0 (previo y de solo lectura) — Diagnóstico de los no fiables
Antes del Paso 2, explicar los 72 casos de la §1 (¿salida del catálogo?, ¿aplazados?, ¿retirados?),
con los datos que ya hay. Sin esa causa, el Paso 2 sería adivinar.

### Paso 2 — Ver la transición a juego (CONDICIONADO a los resultados anteriores)
Solo si el diagnóstico muestra que la causa es la ventana y la planificación, y la proporción de
no fiables sigue siendo alta. Cambio mínimo, **sin esquema**:
- `Collector._catalogue_window`: `market_start_from = now − CLOSE_GRACE` (p. ej. 15 min), para que
  el mercado siga en el catálogo tras la hora publicada.
- `plan_captures` (`cadence.py`): permitir capturas con `−CLOSE_GRACE ≤ minutes_left < 0` mientras
  el mercado no haya pasado a juego, con la cadencia de 1 min.
- `load_market_states` (`repositories.py`): campo nuevo `has_left_prematch` (existe una observación
  con `inplay` o estado distinto de `OPEN`); al observarse, esa observación queda como última y el
  mercado deja de planificarse.
- **Coste:** como mucho 1 observación de transición por mercado y hasta ~15 por los que pasen la
  hora publicada sin entrar en juego. Hay que revisar `estimate_daily_observations`, que ya se
  supera (41,1 frente a 35 de media).
- **Tests:** `test_cadence.py` y el collector (regresión del payload y de la cadencia actual).
- **Riesgo:** **medio**: toca la planificación en producción. Requiere plan de despliegue con
  reinicio controlado (el watchdog ya está activo), y marcha atrás volviendo al commit anterior.
- **Requiere tu aprobación explícita antes de empezar**, y no toca MINIMUM_LIQUIDITY ni los hitos.

## 5. Criterio de «hecho» y medición
- Definición aprobada en `DECISIONS.md`, implementada y con tests.
- **Línea base** (hoy, sin Paso 2): 76 % de los mercados con precios tienen el cierre a ≤ 5 min del
  inicio final publicado; 24 % no. Medida con la hora publicada, no con la transición a juego.
- Objetivo: **proporción de cierres no fiables medible y baja sobre datos nuevos**. El umbral
  numérico de «baja» no está fijado: lo propone la línea base del Paso 1 y lo decides tú **antes**
  de medir.

## 6. Qué no se toca
C2, PostgreSQL operativo salvo la migración 006 aprobada, particionado, cadencia por defecto, hitos,
`MINIMUM_LIQUIDITY`, modelos. Sin backfill. Sin cambios de esquema en observaciones existentes.

## 7. Decisiones que necesito de ti
1. ¿Apruebas la definición de la §3 (en particular el umbral de **5 min** y el tratamiento de
   «no fiable»)?
2. ¿Autorizas el Paso 1 (migración 006 con la vista) y el diagnóstico previo?
3. El Paso 2 queda **sin autorizar** hasta ver los resultados del 1 y del diagnóstico.

## 8. Resultado del Paso 0 (diagnóstico, solo lectura, 2026-11-03)

**Corrección de la línea base.** La cifra del 76 % de la §1 incluía mercados que aún no habían
empezado. Restringida a mercados con inicio publicado anterior a *ahora − 12 h*: **317 mercados,
288 con precios**; cierre candidato a ≤ 5 min del inicio final publicado en **233 (80,9 %)**
(≤ 2 min: 220 · ≤ 10 min: 256). Los **55 restantes (19,1 %)** se explican así:

| Causa (datos existentes) | Mercados | % de 288 | ¿La resuelve el Paso 2? |
|---|---|---|---|
| **B.** El libro se vacía antes del inicio: hay observaciones posteriores sin precios (447 en 21 mercados) o con juego (27 en 2) | 23 | 8,0 % | **No**: no es un problema de ventana. Es la propia realidad del libro |
| **C1.** El catálogo deja de listar el mercado 5–198 min antes del inicio publicado (P50 6,5 min), justo tras la última observación; sin juego observado | 27 | 9,4 % | **Posiblemente**; la causa **no está verificada** |
| **C3.** Aplazados: una sola observación y el inicio publicado se mueve días después | 5 | 1,7 % | No: no hay cierre cercano posible |

- En los cierres fiables, el catálogo deja de verlos ~0,5 min antes del inicio publicado; en C1,
  bastante antes. Hipótesis (**NO VERIFICADO**): el partido empieza antes de lo publicado y el
  mercado sale de la consulta, o el inicio se adelanta en el catálogo. Los datos existentes no
  distinguen entre ellas.
- **Implicación:** el Paso 2 solo podría mejorar como mucho el 9,4 % de C1. Antes de tocar el
  collector conviene la línea base con la vista (Paso 1) y, si se quiere acotar la causa, una
  **consulta puntual de solo lectura a la API** sobre algunos mercados de C1, que requiere tu
  autorización expresa.

## 9. Paso 1 (hecho)
- `migrations/006_market_closing_observation.sql`: solo crea la vista `market_closing_observation`.
- `repositories.closing_observations` y `closing_summary` (solo lectura).
- `tests/test_market_closing_observation.py` (13 tests de integración).
- **Aplicada en producción el 2026-11-03** (`edgecourt db migrate`: esquema 5 → 6, sin migraciones
  pendientes, collector sin tocar y `active`). La vista reproduce la línea base: cohorte desde el
  5-oct, mercados terminados, **233 fiables de 289 con cierre** (la línea base daba 233 de 288; el
  mercado extra cruzó el umbral de 12 h entre ambas mediciones).
- **Toda la historia (476 mercados):** 288 fiables, 107 no fiables y 81 sin cierre. La referencia
  fue el juego observado en 7 mercados y el inicio publicado en 469.

## 10. Consulta puntual a la API sobre los casos C1 (2026-11-03, autorizada por el responsable)

**Qué se hizo.** Una sesión de solo lectura aparte de la del collector (login y logout propios), con
dos operaciones de lectura ya permitidas: `listMarketBook` **sin datos de precios** (estado del
mercado) y `listMarketCatalogue` filtrado por `marketIds`. Sobre 33 mercados: los 28 de C1 y 5
controles fiables recientes. No se escribió nada y el collector no se vio afectado (0 ciclos
fallidos, 10 completados y 0 errores de sesión en los 10 min posteriores).

**Resultado: no concluyente.**
- `listMarketCatalogue` devolvió **0** mercados (los cerrados ya no se listan).
- `listMarketBook` devolvió solo **7 de 33**: Betfair conserva los libros de mercados cerrados
  apenas uno o dos días. De los 28 C1 solo 2 (ambos del 2-nov) seguían disponibles.
- Esos 2 C1 y los 4 controles devueltos tienen el **mismo estado final**: `CLOSED`, `inplay = true`,
  con ganador y perdedor. Es decir, los C1 **sí se jugaron** con normalidad, pero la respuesta
  **no indica cuándo pasaron a juego** (no hay campo de hora de inicio ni de paso a juego).
- Un C1 con 199 min de distancia (2-nov) no se devolvió; **no se sabe por qué** (aplazado,
  retirado u otra causa).

**Qué descarta y qué no.**
- Los C1 no son mercados anulados o inexistentes: los 2 comprobados se jugaron.
- La causa del hueco (partido anticipado, inicio adelantado en el catálogo, otra) **sigue sin
  verificarse**, y una consulta posterior no puede resolverla: la API no conserva ni expone ese
  dato. Solo se puede observar **en el momento**, es decir, con el Paso 2 o con un sondeo
  prospectivo de solo lectura que siga unos mercados reales alrededor de su hora de inicio.

**Consecuencia para el Paso 2.** Sigue **sin autorizar**. La mejora máxima esperable es el 9,4 %
(C1) y la hipótesis que la justificaría no está demostrada. Opciones, a decidir por el responsable:
dejarlo aparcado y medir con la vista sobre datos nuevos; o un sondeo prospectivo acotado (sin
tocar el collector) antes de decidir.


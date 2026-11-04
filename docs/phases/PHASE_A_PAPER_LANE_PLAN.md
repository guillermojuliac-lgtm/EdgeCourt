# Plan del Carril A — Paper betting operativo

> **Estado (2026-11-03): [DEC-022](../DECISIONS.md#dec-022) ACEPTADA; A0 HECHO (estrategia v1
> congelada como provisional); A1 COMMITEADO (`f17a7c1`, pendiente de `/validar-todo`); A2 IMPLEMENTADO (alias de Cerundolo confirmado; `--apply` pendiente de ejecutar por el responsable). Siguiente paso: A3.** El Carril B (estudio
> y mejora) sigue su propio ritmo; este plan no lo toca. **Dinero real: fuera de alcance**
> ([DEC-001](../DECISIONS.md#dec-001)).

## 1. Objetivo
Ver, desde la terminal, apuestas **ficticias** de tenis (ATP individual prematch) colocadas con
una estrategia fijada de antemano, liquidadas con el resultado real y medidas contra el cierre
(CLV). Es una línea mínima de extremo a extremo: sirve para descubrir fallos operativos y para
empezar a acumular muestra. **No** es una prueba de ventaja (ver §8).

## 2. Qué existe y qué falta (verificado en el código)

| Pieza | Estado |
|---|---|
| Collector, precios con retraso, cierre medible (DEC-021) | ✅ en producción |
| Tablas `prediction`, `paper_bet`, `bet_settlement` (inmutables, con cadena de hashes) | ✅ existen, vacías |
| Modelo de regresión logística (2 en `models/challenger/`, ninguno promovido) | ✅ entrenado el 18-sep |
| Límites de riesgo en `config.py` (bankroll 1.000, stake ≤ 1 %, exposición diaria ≤ 5 %, por mercado ≤ 2 %, drawdown 20 %, Kelly ≤ 0,25) | ✅ validados, sin uso |
| `paper/`, `risk/`, `value/`, `notifications/` | ❌ vacíos |
| `betfair_runner.player_id` | ❌ NULL en todas las filas |
| Datos de partidos | ⚠️ **ingeridos el 18-sep**: hay que refrescarlos para predecir partidos actuales |
| Features para un partido futuro | ⚠️ el constructor trabaja por lotes sobre el histórico; falta calcular «a fecha de hoy» para una pareja de jugadores |
| Comandos `predict`, `paper status`, `metrics` | ⚠️ reservados, devuelven «no implementado» |

## 3. Estrategia v1 — a fijar ANTES de la primera apuesta

**Estrategia v1 CONGELADA como provisional el 2026-11-03** ([`PHASE_A_STRATEGY_V1_PROPOSAL.md`](PHASE_A_STRATEGY_V1_PROPOSAL.md), SHA-256 `32fa7eda9958d76b9a77335aa80ba407a0a78f40ec547ec337ee8306d07b6b63`). Los valores de abajo son los de esa versión.
Cada punto es una **propuesta**; el responsable decide. Una vez fijada, cualquier cambio es la
versión v2, con su propio historial. Las cifras no son afinadas con datos paper.

| Parámetro | Propuesta | Motivo |
|---|---|---|
| Alcance | ATP **individuales**, prematch, mercado `MATCH_ODDS` | [DEC-020](../DECISIONS.md#dec-020) |
| Lado | Solo `BACK` | Simplifica la liquidez y la responsabilidad |
| Desvigado | **Abierto**: proporcional, potencia o Shin. Hay que elegir uno | Es una decisión pendiente de `METRICS.md` |
| Umbral de valor | **Abierto**: edge mínimo después de comisión | Sin elegir con datos paper |
| Comisión supuesta | La del Exchange español, a confirmar en la cuenta | Entra en el EV después de costes |
| Momento de apostar | **Abierto**: p. ej. una sola decisión por mercado a X min del inicio | Evita apostar repetidas veces el mismo error |
| Stake | **Plano** (1 % del bankroll ficticio) | Kelly necesita probabilidades calibradas y estables |
| Liquidez mínima | `MINIMUM_LIQUIDITY` = 50 ([DEC-012](../DECISIONS.md#dec-012)) | Sin cambios |
| Una apuesta por mercado | Sí | Evita duplicar exposición |
| Exclusiones | Jugadores sin identidad segura, sin historial suficiente o sin precios BACK y LAY | Se cuentan y se muestran |
| Fills | Se asume casado al precio visto en la observación (con retraso) | **Optimista**; se declara |

## 4. Pasos

Tamaños relativos: S (pequeño), M (medio), L (grande). No hay fechas.

### A0 — Decisiones y estrategia v1 · S · solo documentos
Aceptar DEC-022 y fijar por escrito la estrategia v1 (§3). **Sin código.**

### A1 — Datos al día y features «a fecha de hoy» · M
- Refrescar el dataset de partidos (fuente TennisMyLife) y automatizar la actualización.
- Función de features y Elo «a fecha de hoy» para una pareja de jugadores, reutilizando
  `PlayerHistory` y el Elo existentes; sin ver nunca datos posteriores.
- Tests: sin fuga temporal, coincide con el constructor por lotes en partidos pasados.
- **Riesgo:** medio. Si el refresco falla, las predicciones se basan en datos viejos: el estado lo
  debe mostrar.

### A2 — Identidad de jugadores (Fase 8b) · M
- Mapeo `betfair_runner.player_id` determinista, **sin fuzzy silencioso**; los dudosos se
  descartan y se cuentan.
- Cobertura medida sobre los mercados ATP ya capturados.
- **CLI:** `edgecourt identity report` (cobertura y descartados).
- **Dependencia:** A1 (catálogo de jugadores actual).

### A3 — Predicciones · M
- Promoción **manual** de un modelo a `production` ([DEC-013](../DECISIONS.md#dec-013)) y registro
  en `model_version`.
- Tarea que escribe `prediction` para los mercados próximos con identidad resuelta.
- **CLI:** `edgecourt predict` (hoy reservado).
- **Dependencias:** A1, A2.

### A4 — Valor y riesgo (funciones puras) · M
- Desvigado, edge, EV después de comisión y stake plano con los límites de `config.py`
  (exposición diaria, por mercado, drawdown).
- Tests de propiedades: nunca supera los límites, nunca stake negativo, determinista.
- **CLI:** `edgecourt paper candidates` (qué apostaría, **sin escribir nada**).

### A5 — Ledger paper · M
- Escritura en `paper_bet` con cadena de hashes, libro de precios congelado, idempotencia y una
  apuesta por mercado.
- **Migración nueva:** columna de **versión de estrategia** (tablas vacías) y verificación de la
  cadena de hashes.
- **CLI:** `edgecourt paper bets`, `edgecourt paper status`.

### A6 — Liquidación y CLV · M
- Liquidar con el resultado real del mercado (ganador/perdedor). **Atención:** Betfair solo
  conserva los libros de mercados cerrados **1–2 días**; la liquidación debe ejecutarse **a diario**
  y avisar si se pierde un resultado.
- CLV con el cierre de [DEC-021](../DECISIONS.md#dec-021), marcando los cierres no fiables.
- **CLI:** `edgecourt paper pnl`, `edgecourt paper clv`.

### A7 — Operación diaria · S–M
- Temporizador de systemd para predecir, apostar en paper y liquidar, con watchdog/estado como el
  collector. Telegram opcional (resumen diario).
- Aviso visible cuando algo no se ha ejecutado (sin predicciones, sin liquidar, datos viejos).

## 5. Interfaz de terminal (boceto, no implementado)

```
$ edgecourt paper status
PAPER · estrategia v1 · datos retrasados · ejecución supuesta
  bankroll ficticio      : 1 000,00 → 1 012,40   (drawdown máx. 1,8 %)
  apuestas               : 14 (11 liquidadas · 3 abiertas)
  ROI sobre stake        : +3,1 %        (n=11: NO CONCLUYENTE)
  CLV medio              : +0,8 %        (n=9 cierres fiables · 2 no fiables)
  último dato de mercado : hace 1 min    · último resultado liquidado: hace 6 h
  excluidos              : 38 sin identidad · 5 sin precios

$ edgecourt paper bets --open
  mercado     partido                    precio  stake   edge   inicio
  1.2649...   Jugador A v Jugador B      2,18    10,00   +4,2%  hoy 14:00
```

Cada informe repite la etiqueta «paper, datos retrasados, ejecución supuesta» y el tamaño de
muestra. Una interfaz web, si hace falta, es posterior y opcional.

## 6. Dependencias y orden
`A0 → A1 → A2 → A3 → A4 → A5 → A6 → A7`. A4 puede empezar en paralelo a A2/A3 porque son
funciones puras. Cada paso se entrega con tests, documentación y una orden de CLI que se pueda
ejecutar.

## 7. Qué no se toca
El collector y sus datos, `MINIMUM_LIQUIDITY`, las migraciones aplicadas, 3.5-B (Paso 2 aparcado) y
el Carril B. El carril A **lee** del collector; nunca lo modifica ni lo detiene.

## 8. Qué NO demuestra este carril
- **Optimismo estructural:** precios con 1–180 s de retraso, sin saber si la apuesta se habría
  casado y con profundidad mediana de ~20 €. El resultado real sería peor.
- **Ruido:** con pocas semanas de apuestas, el ROI es ruido. La señal estadística (≈ 8.700 apuestas
  para el ROI) está lejos; el CLV necesita menos muestra pero depende de un cierre fiable.
- **Volumen:** la oferta ATP en `.es` es desigual (Shanghái 132 individuales, Pekín 3, Tokio 0);
  habrá semanas con pocas o ninguna apuesta.
- Nada de esto autoriza dinero real: eso exigiría una DEC nueva basada en el criterio de
  [`METRICS.md`](../METRICS.md), la clave Live y código de ejecución que hoy no existe.

## 9. Riesgos
| Riesgo | Mitigación |
|---|---|
| Ajustar la estrategia mirando los resultados paper | Versiones, estrategia por escrito antes, sin cambios retroactivos |
| Identidad errónea de un jugador | Determinista, dudosos descartados y contados, cobertura visible |
| Pérdida de resultados por la retención de Betfair | Liquidación diaria con aviso |
| Predecir con datos viejos | Estado explícito de «antigüedad del dato» |
| Que el paper interfiera con el collector | Solo lectura sobre sus tablas; proceso separado |

## 10. Pendiente de decidir por el responsable
1. ~~DEC-022~~ **aceptada el 2026-11-03.**
2. ~~Estrategia v1~~ **congelada como provisional (2026-11-03).** Quedan por confirmar la comisión y el stake mínimo de tu cuenta.
3. ~~¿A1 o A2?~~ Se empieza por **A1** (datos al día).

## 11. Resultado de A1 (2026-11-03)

- **Datos al día:** `2026.csv` descargado de TennisMyLife y dataset reconstruido con
  `data import --from-year 1990`: **114.132 partidos (+588)**, último partido **2026-11-01**. Los
  años anteriores no cambian (mismos errores de validación ya documentados: 139 superficies nulas,
  1 ronda nula). Copia de seguridad previa en el scratchpad de la sesión.
- **Refactor del constructor:** la lectura/emisión de features y la actualización del estado se
  extraen a `_emit_features`, `_record_match` y `_remember_attributes`; `build_features` conserva su
  firma. **Comprobado** que la tabla de features y la de Elo son **idénticas** (114.132 filas)
  antes y después del cambio.
- **Módulo nuevo `features/asof.py`:** `build_asof_state` (≈38 s sobre toda la historia) y
  `features_for_pair` (23 features de un partido futuro). Rechaza fechas anteriores al último
  partido del estado. Contra el lote: **0 diferencias** en las 23 features sobre 3 partidos
  reales de 2026.
- **Comando nuevo:** `edgecourt data freshness [--max-days 14]` (solo lectura, sale con 1 si el
  dataset está obsoleto).
- **Limitación declarada:** para un partido futuro, ranking, puntos, edad y altura son los **del
  último partido de cada jugador** (edad avanzada hasta la fecha), no el ranking semanal actual.
  El resultado lo indica (`attribute_source`, `attribute_age_days`).
- **Tests escritos, no ejecutados:** `tests/test_features_asof.py` (10) y
  `tests/test_data_freshness.py` (2). Diferidos a `/validar-todo`, que se recomienda antes de
  confirmar el cambio porque toca `builder.py` y `elo.py`, que alimentan el entrenamiento.
- **El modelo no se ha reentrenado:** sigue siendo el del 18-sep (entrenado hasta 2022); decidir si
  reentrenar es del carril B / A3.

## 12. Resultado de A2 (2026-11-03): identidad de jugadores

- **Datos de Betfair:** `betfair_runner.runner_name` trae el **nombre completo** («Alexander
  Zverev»); es el nombre abreviado de los *eventos* («A Zverev v …») el que no sirve. Se usa el del
  runner.
- **Reglas** (`identity/players.py`), deterministas y sin fuzzy silencioso: (1) alias revisado por
  una persona; (2) nombre normalizado **exacto** (sin acentos, guiones ni mayúsculas) entre los
  jugadores **activos** (partido en los últimos 3 años); (3) un candidato = resuelto, varios =
  ambiguo, ninguno = sin coincidencia. Los dudosos se descartan y se cuentan; se listan candidatos
  por apellido **solo para revisar**, no se aplican.
- **Superficie** (`identity/tournaments.py`): «ATP Shanghai 2026» → clave «shanghai» → superficie de
  las últimas 3 ediciones del dataset, solo si coinciden. Fuera de alcance en v1: Copa Davis, Laver
  Cup, ITF, WTA, Grand Slams y dobles.
- **Resultado en la base operativa** (informe, solo lectura): 477 mercados → 42 dobles, 279 fuera de
  alcance (WTA, Copas, ITF) y el resto ATP individual. Antes del alias: 151 listos y 5 sin resolver
  (todos «Manuel Cerundolo»; candidatos C0AU Francisco y C0C8 Juan Manuel Cerundolo).
  **Alias confirmado por el responsable el 2026-11-03** («Manuel Cerundolo» = C0C8). Con él:
  **156 mercados listos y 312 de 312 runners resueltos.**
- **Escritura:** `edgecourt identity resolve --apply` rellena `betfair_runner.player_id` **solo si
  está vacío** (nunca sobrescribe) y es idempotente. **Pendiente de ejecutar por el responsable**:
  el sistema de permisos denegó la escritura en la base operativa desde la sesión de Claude. La base
  no se ha modificado.
- **Tests:** `tests/test_identity.py` (20) pasa (20 passed). La suite completa, diferida a `/validar-todo`.


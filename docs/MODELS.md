# Modelos y features

> Estado: PHASE 0. El catálogo de features se rellena en PHASE 3 y los resultados
> comparativos a partir de PHASE 4. Aquí se fijan de antemano las reglas del juego.

## Catálogo de modelos

| ID | Modelo | Papel | Fase |
|---|---|---|---|
| **MODEL 0** | Probabilidad implícita de Betfair | **Benchmark a batir.** Es el precio que debemos considerar eficiente por defecto. | 8 |
| **MODEL 1** | Elo global + Surface Elo | Baseline independiente del ML. Si el ML no lo bate, el ML sobra. | 2 |
| **MODEL 2** | Regresión logística | Baseline lineal, interpretable, sobre las features de PHASE 3. | 4 |
| **MODEL 3** | XGBoost | Captura no linealidades e interacciones. | 5 |

LightGBM, CatBoost y ensembles quedan **fuera de alcance** hasta que exista una justificación
concreta. Añadir modelos multiplica las oportunidades de sobreajustar por selección.

## Contrato de un modelo de probabilidad

Todo modelo expone la misma interfaz, lo que los hace intercambiables y comparables:

```python
predict_proba(features) -> (p_a, p_b)    con p_a + p_b == 1
```

Reglas:

- Un modelo **nunca** ve cuotas, bankroll ni stake. Solo devuelve probabilidad.
- Un modelo **nunca** ve datos con fecha ≥ la del partido que predice.
- Toda salida se somete a `test_probability_sum`.

## Versionado

Cada modelo entrenado se acompaña de un manifiesto JSON: identificador, fecha de
entrenamiento, rango temporal de los datos, lista y hash de features, hiperparámetros, hash del
artefacto binario y métricas de validación. Las predicciones del ledger guardan
`model_version`, de modo que cualquier resultado sea atribuible a un modelo concreto.

El binario del modelo **no se versiona en git** (ver `.gitignore`): se identifica por hash.

## Política de features

Tres reglas, en orden de importancia:

1. **Nada que no se conociera antes del partido.** Toda estadística de rendimiento entra
   únicamente como agregado histórico desplazado (`shift(1)` por jugador en orden cronológico).
   Las columnas `raw_*` de `match_facts` no son accesibles al generador de features.
2. **Toda feature es relativa A−B** y antisimétrica: intercambiar A y B debe invertir el signo.
   Verificado por `test_features_antisymmetry`.
3. **Ninguna feature entra "porque está disponible".** Cada una se documenta abajo con
   definición, ventana, justificación y política de nulos. Sin justificación, no entra.

## Catálogo de features — implementado (PHASE 3)

21 features relativas A−B, más 5 columnas de contexto. Todas se generan en una **única pasada
cronológica** con el orden estricto: leer estado previo → emitir features → *solo entonces*
incorporar el partido al historial. El no-leakage no depende de recordar aplicar un `shift`:
depende del orden de las operaciones, fijado por tests de envenenamiento.

| Feature | Definición | Ventana | Justificación | Cobertura |
|---|---|---|---|---|
| `ranking_diff` | rank A − rank B | — | Valoración oficial del circuito. Signo invertido: menor ranking es mejor | 97,4 % |
| `rank_points_diff` | puntos ATP A − B | — | Escala continua, sin los saltos discretos de la posición | 94,9 % |
| `age_diff` | edad A − B | — | Curva de rendimiento por edad | 99,2 % |
| `height_diff` | altura A − B | — | Proxy de potencia de saque | 95,2 % |
| `elo_diff` | Elo global A − B | todo el historial | Mejor predictor individual (AUC 0,730) | 99,9 % |
| `surface_elo_diff` | Elo de superficie A − B | por superficie | Especialización por superficie | 96,9 % |
| `winrate_last_{5,10,20}_diff` | % victorias A − B | 5/10/20 partidos | Forma reciente a tres horizontes | 89,8 / 84,0 / 75,8 % |
| `surface_winrate_diff` | % victorias en la superficie | ≥5 partidos | Adaptación a la superficie | 80,0 % |
| `first_serve_pct_diff` | 1ºs saques dentro / puntos al saque | 20 partidos | Fiabilidad del primer saque | 89,9 % |
| `first_serve_points_won_diff` | puntos ganados con 1º saque | 20 partidos | Eficacia del saque principal | 89,9 % |
| `second_serve_points_won_diff` | puntos ganados con 2º saque | 20 partidos | Solidez bajo presión; discrimina bien (AUC 0,643) | 89,9 % |
| `return_points_won_diff` | puntos ganados al resto | 20 partidos | Derivada del saque del rival | 89,9 % |
| `break_points_saved_diff` | % bolas de break salvadas | 20 partidos | Rendimiento en puntos decisivos | 89,8 % |
| `break_points_converted_diff` | % bolas de break convertidas | 20 partidos | Capacidad de romper el saque | 89,5 % |
| `aces_rate_diff` | aces / puntos al saque | 20 partidos | Potencia de saque | 89,9 % |
| `double_fault_rate_diff` | dobles faltas / puntos al saque | 20 partidos | Fragilidad del saque | 89,9 % |
| `days_since_last_match_diff` | días desde el último partido | — | Descanso y falta de ritmo | 97,0 % |
| `matches_last_{7,14}_days_diff` | partidos disputados | 7/14 días | Fatiga acumulada | 100 % |
| `minutes_played_last_7_days_diff` | minutos jugados | 7 días | Fatiga ponderada por duración | 100 % |
| `head_to_head_before_match` | victorias A − victorias B | histórico del par | Emparejamientos de estilo | 100 % |

Contexto (conocido antes del partido, uso legítimo): `tourney_level`, `surface`, `round`,
`indoor`, `best_of`. Más `player_{a,b}_matches_before`, para poder filtrar partidos con
historial insuficiente.

### Decisiones

- **Ventana de 20 partidos** para las estadísticas de servicio y resto: media temporada de un
  jugador de tour. Suficiente para promediar el ruido, corta para seguir reflejando el estado
  actual.
- **Mínimos de muestra**: 5 partidos para la forma reciente, 5 para el winrate por superficie,
  3 observaciones para una media de estadísticas. Por debajo, la feature es nula.
- **Sin imputación.** Un valor ausente se propaga como nulo. Imputar es decisión del pipeline
  del modelo, explícita y documentada, no algo que deba ocurrir a escondidas aquí.
- **Métricas de resto derivadas del saque del rival**: los puntos que el rival no ganó con su
  servicio son los que el jugador ganó al resto. Es la única forma de obtenerlas del formato.
- **Los walkovers no alimentan el historial.** No se jugó: no hay información de rendimiento.

### Verificación de ausencia de leakage sobre datos reales

Además de los tests de envenenamiento, se midió el poder predictivo univariante de cada feature
sobre TRAIN 2000–2022. Si alguna filtrase el resultado, su AUC se dispararía.

**Máximo observado: 0,7298** (`elo_diff`). Todos los valores son plausibles para tenis y, lo
más importante, **todas las direcciones tienen sentido físico**: `ranking_diff` sale invertido
(AUC 0,302) porque un ranking menor es mejor, y `double_fault_rate_diff` también (0,441) porque
más dobles faltas es peor. Un generador con leakage no produce este patrón coherente.

**Control positivo.** Se midió qué ocurriría usando las mismas métricas pero tomadas del
partido que se predice — el error clásico del análisis de tenis:

| Métrica | Del propio partido (leakage) | Media móvil previa (EdgeCourt) |
|---|---|---|
| `first_serve_points_won` | **0,9171** | 0,6242 |
| `break_points_saved` | 0,7525 | 0,5773 |
| `aces_rate` | 0,7090 | 0,5674 |

Un AUC de 0,92 con una sola variable es exactamente el espejismo que este módulo existe para
evitar. La diferencia de casi 0,30 en AUC cuantifica el tamaño de la trampa.

### Distribución

Las 21 features tienen media ≈ 0 sobre los 113.544 partidos, lo que confirma la antisimetría y
la aleatorización A/B en datos reales: ningún lado está sistemáticamente favorecido.

## Elo y Surface Elo — implementado (PHASE 2)

Cuatro ratings por jugador: global, hard, clay, grass. Actualización en **una sola pasada
cronológica** siguiendo `order_key`. Para cada partido se persiste el estado *previo*.

### Decisiones

| Decisión | Razón |
|---|---|
| **K dinámico** `K = k_base / (n + 5)^0.4` | Un jugador con 5 partidos tiene un rating mucho más incierto que uno con 500. Un K constante haría oscilar demasiado al veterano y converger demasiado lento al novato |
| **Los walkovers no actualizan** | No se jugó: no hay información sobre quién es mejor |
| **Los abandonos sí actualizan** | Hubo partido y hubo un ganador |
| **Carpet sin Elo propio** | Circuito extinto y muestra residual; esos partidos alimentan el Elo global y ningún Elo de superficie |
| **Sin ajuste de parámetros** | Se usan valores estándar del tenis. Ajustarlos convertiría el benchmark en un modelo más, y un benchmark debe ser honesto, no óptimo |

### Propiedad: solo importa el ratio `k_base / scale`

Los ratings crecen proporcionalmente a `K`, y la probabilidad depende de `diff / scale`. Por
tanto `(k_base=100, scale=400)` y `(k_base=150, scale=600)` son **el mismo modelo**, cosa
verificada empíricamente (métricas idénticas hasta el último decimal) y fijada en
`test_only_the_k_to_scale_ratio_matters`. El sistema tiene un grado de libertad, no dos: toda
búsqueda futura de hiperparámetros debe recorrer el ratio y no la rejilla completa.

### Resultados del benchmark

Filtrado a partidos con ≥10 partidos previos de ambos jugadores, y excluyendo walkovers.

**VALIDATION 2023** (2.481 partidos)

| modelo | Brier | Log Loss | Accuracy* | AUC | ECE |
|---|---|---|---|---|---|
| elo_blend_50 | **0.22534** | **0.64459** | 0.6348 | 0.6916 | 0.0559 |
| elo_global | 0.22711 | 0.65022 | 0.6348 | 0.6883 | 0.0610 |
| elo_surface | 0.23182 | 0.66224 | 0.6260 | 0.6807 | 0.0774 |
| moneda | 0.25000 | 0.69315 | 0.5010 | 0.5000 | 0.0010 |

**TEST 2024–2025** (5.104 partidos)

| modelo | Brier | Log Loss | Accuracy* | AUC | ECE |
|---|---|---|---|---|---|
| elo_blend_50 | **0.22173** | **0.63428** | 0.6350 | 0.6996 | 0.0551 |
| elo_global | 0.22222 | 0.63579 | 0.6391 | 0.6992 | 0.0529 |
| elo_surface | 0.22876 | 0.65274 | 0.6356 | 0.6863 | 0.0665 |
| moneda | 0.25000 | 0.69315 | 0.4998 | 0.5000 | 0.0002 |

\* La accuracy se reporta **solo como referencia** y no se optimiza (docs/METRICS.md).

Brier skill score frente a la moneda: **+9,9 %** en VALIDATION y **+11,3 %** en TEST. El
resultado es estable entre ambos conjuntos, lo que es más informativo que su magnitud.

### Hallazgo principal: el Elo está sistemáticamente sobreconfiado

La calibración por buckets muestra un patrón monótono e inequívoco en los dos conjuntos: donde
el Elo predice poco, ocurre más de lo dicho; donde predice mucho, ocurre menos.

TEST 2024–2025, `elo_global`:

| bucket | n | predicha | observada | gap |
|---|---|---|---|---|
| [0.0, 0.1) | 168 | 0.0652 | 0.1131 | **+0.0479** |
| [0.1, 0.2) | 396 | 0.1546 | 0.2323 | **+0.0777** |
| [0.3, 0.4) | 672 | 0.3513 | 0.4345 | +0.0833 |
| [0.4, 0.5) | 692 | 0.4514 | 0.4523 | +0.0009 |
| [0.6, 0.7) | 727 | 0.6495 | 0.5763 | −0.0732 |
| [0.8, 0.9) | 395 | 0.8477 | 0.7696 | **−0.0780** |
| [0.9, 1.0) | 157 | 0.9375 | 0.8917 | −0.0457 |

**Implicación para el proyecto:** un ECE de ~0.055 es de la magnitud del edge que se pretende
detectar (el umbral por defecto es 0.03). Apostar con estas probabilidades sin calibrar
produciría "value" allí donde solo hay error de calibración, sobre todo en los favoritos
claros, que es justo donde el sesgo es mayor. **El Elo crudo no es utilizable como generador
de probabilidades para el Value Engine.** Sirve como benchmark de ordenación, que es su papel.

### El compromiso entre discriminación y calibración

Exploración sobre VALIDATION (nunca sobre TEST), variando el único grado de libertad real:

| ratio `k_base/scale` | Brier | ECE | AUC |
|---|---|---|---|
| 0.625 (250/400) | **0.22711** | 0.0610 | **0.6883** |
| 0.250 (100/400) | 0.22771 | 0.0442 | 0.6786 |
| 0.150 (60/400) | 0.22879 | 0.0247 | 0.6698 |
| 0.075 (30/400) | 0.23106 | **0.0197** | 0.6578 |

Bajar K reduce el ECE a un tercio, pero cuesta discriminación (AUC 0.688 → 0.658) y empeora el
Brier. **Se conserva el K alto a propósito.** La discriminación es la parte difícil y no se
puede recuperar después; la calibración sí se arregla a posteriori con Platt o isotónica, que
es exactamente el trabajo de PHASE 6. Bajar K para maquillar el ECE habría sido tirar
información a cambio de un número más bonito.

### Elo de superficie: peor solo, útil mezclado

`elo_surface` es la peor variante en solitario en ambos conjuntos, algo esperable: reparte los
mismos partidos entre tres ratings, así que cada uno converge más despacio. Pero la mezcla
50/50 bate a ambos componentes en Brier y Log Loss en los dos conjuntos, lo que indica que
aporta señal propia. La mezcla se hace **en espacio de Elo**, donde es lineal, no en espacio de
probabilidad.

El peso 0.5 es un valor por defecto razonable, no un óptimo ajustado. Optimizarlo se deja para
cuando haya un modelo de verdad que lo consuma.

## Calibración *(PHASE 6)*

- **Sigmoid/Platt** cuando la muestra es limitada.
- **Isotónica** cuando hay muestra suficiente (más flexible, más propensa a sobreajustar).
- Ajustada sobre un **conjunto de calibración temporalmente separado**, nunca sobre el de
  entrenamiento.
- Se reporta ECE y curva de calibración antes y después.

## Validación

Validación **cronológica siempre**; el split aleatorio está prohibido como validación
principal. Walk-forward a partir de PHASE 7: reentrenar por ventana y evaluar la siguiente,
sin que ninguna ventana de entrenamiento contenga datos posteriores a su fecha de evaluación.

Los hiperparámetros se buscan **solo sobre VALIDATION**, con presupuesto de iteraciones
registrado, para que el grado de búsqueda sea auditable.

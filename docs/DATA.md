# Datos

> Estado: PHASE 0. El esquema canónico y el split se fijan en PHASE 1.
> **No se ha descargado ningún dataset.** Ninguna descarga ocurre sin decisión explícita.

## Fuentes candidatas

Listadas para evaluación. **Antes de usar cualquiera hay que verificar sus términos y licencia
en origen**: lo indicado aquí es orientativo y puede haber cambiado.

| Fuente | Contenido | Formato | Notas |
|---|---|---|---|
| `JeffSackmann/tennis_atp` y `tennis_wta` (GitHub) | Partidos ATP/WTA desde 1968, con estadísticas de servicio/resto por partido, rankings, datos de jugador | CSV por año | Estándar de facto en investigación de tenis. Licencia declarada por el autor como no comercial (verificar el `LICENSE` del repo antes de usar). Cobertura de estadísticas detalladas mucho mejor a partir de ~1991 y aún mejor desde ~2000. |
| `tennis-data.co.uk` | Partidos con cuotas de **casas de apuestas** (no exchange) desde 2000 | Excel/CSV por año | Útil como *proxy* de precio de mercado, pero **no es Betfair**: márgenes distintos, sin lay ni liquidez. No sustituye al exchange. |
| Betfair Historical Data (comercial) | Ficheros de mercado del exchange | propietario | Es la única fuente que permitiría backtestear CLV y ROI de verdad. De pago. Decisión pendiente (ver R4 del plan). |
| Feeds comerciales punto-a-punto | Datos por punto/juego | varios | Fuera de alcance en esta fase. |

**Prohibido:** scraping que incumpla los términos de servicio de un sitio, y datasets de
procedencia dudosa o sin licencia identificable.

## Limitación estructural: no hay histórico del exchange

Sin histórico de Betfair, **no se puede backtestear ROI ni CLV sobre años pasados**. El
backtest temporal (PHASE 7) valida exclusivamente calidad probabilística. La validación
económica es *forward-only* y empieza cuando el collector lleve tiempo funcionando.

Consecuencia operativa: **el collector se pone en marcha lo antes posible**, aunque el modelo
no esté terminado, porque su valor es el tiempo acumulado.

## Esquema canónico `match_facts` *(PHASE 1)*

Un partido, una fila, inmutable. Campos previstos:

**Identidad y contexto**
`match_id`, `date`, `tourney_id`, `tourney_name`, `tourney_level`, `surface`,
`indoor` (bool), `round`, `best_of`, `order_key`

**Jugadores** (ya con asignación A/B aleatorizada, ver más abajo)
`player_a_id`, `player_a_name`, `player_a_rank`, `player_a_rank_points`, `player_a_age`,
`player_a_hand`, `player_a_height` — e idénticos para `player_b_*`

**Resultado**
`target` (1 si gana A), `score`, `sets_a`, `sets_b`, `minutes`, `completion_status`

**Estadísticas del partido** — ⚠️ **prefijo `raw_`, prohibidas como feature directa**
`raw_a_aces`, `raw_a_dfs`, `raw_a_svpt`, `raw_a_1st_in`, `raw_a_1st_won`, `raw_a_2nd_won`,
`raw_a_bp_saved`, `raw_a_bp_faced`, y equivalentes para B.

### Por qué el prefijo `raw_`

Estas columnas describen **lo que ocurrió en el partido que queremos predecir**. Usarlas como
feature produce un modelo con AUC ≈ 0.99 y valor predictivo real nulo. El prefijo las hace
visibles, y el generador de features solo puede consumirlas a través de la ruta de agregación
histórica (medias móviles desplazadas), nunca directamente. `test_no_future_data_leakage` lo
verifica.

## Políticas de tratamiento

### Aleatorización A/B
Los datasets crudos traen columnas `winner_*` / `loser_*`, con lo que el target sería siempre 1
y el modelo aprendería el orden de las columnas. En la ingesta, la asignación A/B se decide por
**hash determinista del `match_id`**: reproducible entre ejecuciones y equilibrada
(`P(target=1) ≈ 0.5`, verificado por test).

### Clave de orden
Dos partidos del mismo jugador con la misma fecha podrían ordenarse de forma que el Elo de uno
incorpore el resultado del otro. `order_key = (date, tourney_id, round_order, match_num)`
define un orden total y determinista. Es la única clave que puede usar el actualizador de Elo.

### Retiros y walkovers (`completion_status`)
Valores: `completed`, `retired`, `walkover`, `defaulted`, `unknown`.
Política por defecto: **excluidos de entrenamiento y de las métricas de calibración**; se
conservan en el dataset y se reportan aparte. Un retiro en el primer set no es un resultado
predecible y contamina tanto el Elo como el target. Además, Betfair aplica reglas propias de
anulación en estos casos, que el ledger deberá reflejar.

### Valores ausentes
Ningún imputado silencioso en `match_facts`. La imputación es decisión del pipeline de modelo,
explícita y documentada en `docs/MODELS.md`.

## Split temporal *(a fijar en PHASE 1, y después inmutable)*

| Conjunto | Años | Uso |
|---|---|---|
| TRAIN | 2000–2022 | entrenamiento |
| VALIDATION | 2023 | toda la iteración de desarrollo y ajuste |
| TEST | 2024+ | evaluación final, consultas **contadas y registradas** |

Rango exacto sujeto a la cobertura real del dataset importado.

El uso repetido del TEST para tomar decisiones es leakage humano y produce optimismo
sistemático. Por eso: el desarrollo usa **solo** VALIDATION, y cada evaluación sobre TEST se
registra en `data/results/test_evaluations.jsonl` con fecha, modelo y motivo.

## Almacenamiento

- `data/raw/` — ficheros originales tal cual llegaron, nunca modificados.
- `data/processed/matches/` — `match_facts` en Parquet particionado por año.
- `data/odds/` — snapshots de Betfair, particionados por fecha.
- `data/results/` — métricas, informes y registro de evaluaciones.

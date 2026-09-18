# Datos

> Estado: PHASE 0. El esquema canónico y el split se fijan en PHASE 1.
> **No se ha descargado ningún dataset.** Ninguna descarga ocurre sin decisión explícita.

## Fuentes: investigación de 2026-09-18

### Hallazgo: la fuente de referencia del sector ha desaparecido

Los repositorios `JeffSackmann/tennis_atp` y `JeffSackmann/tennis_wta` —el estándar de facto
en investigación de tenis durante más de una década— **ya no existen públicamente**. Verificado
vía API de GitHub: la cuenta sigue activa pero conserva un único repositorio
(`tennis_MatchChartingProject`, datos punto a punto, **sin licencia declarada**). El propio
`tennisabstract.com` aún enlaza a un GitHub que ya no sirve esos datasets.

Consecuencia: **no existe una fuente Sackmann viva**. Cualquier uso de esos datos pasa por
mirrors de terceros, que por definición no se actualizan con los partidos nuevos.

### Fuentes verificadas

Cada una comprobada por HTTP el 2026-09-18. "Viva" = sigue incorporando partidos nuevos.

| Fuente | Cobertura | Stats servicio/resto | Viva | Licencia | Estado verificado |
|---|---|---|---|---|---|
| **TennisMyLife** (`stats.tennismylife.org/data/`) | ATP 1968–2026, WTA 1990–2026, Challenger, Qualy | Sí | **Sí** | MIT declarada | 2.132 partidos en 2026, último torneo 2026-08-30. ~3.000 partidos/año. `robots.txt` permite `/data/`; el sitio publica scripts de descarga |
| `Aneeshers/tennis-sackmann-archive` | ATP + WTA + slam point-by-point, hasta jun-2026 | Sí | No | CC BY-NC-SA 4.0 explícita | Mirror archivístico, 145 MB, último push 2026-06-25 |
| `farhadGithub/tennis-atp-data` | ATP singles 1968–2024 | Sí | No | CC BY-NC-SA 4.0 | Mirror de segunda mano (re-hospeda desde otro mirror), 7,9 MB |
| `Tennismylife/TML-Database` (GitHub) | ATP 1968–2026 | Sí | **No** | **Ninguna** | Se anuncia "live updated" pero el último commit es de 2026-01-27: `2026.csv` tiene 137 filas frente a 2.132 en el sitio web. **El repo está obsoleto; usar el sitio** |
| `datahub.io/core/atp-world-tour-tennis-data` | 1877–2017 | **No** (solo resultados) | No | CC BY 4.0 | Licencia limpia pero sin estadísticas: inútil para nuestras features |
| Kaggle `guillemservera/tennis` | MatchStats 1991– | Sí | ? | CC BY-NC-SA 4.0 | Requiere cuenta. Misma licencia y cobertura que Sackmann: casi con seguridad un re-empaquetado |
| `jasonmauss/tennis_atp` | hasta 2015 | Sí | No | Ninguna | Obsoleto |
| `tennis-data.co.uk` | Cuotas de casas desde 2000 | No | ? | — | **HTTP 503**: sitio caído en el momento de la comprobación |
| APIs comerciales (tennis-api.com, Apify…) | Amplia | Sí | Sí | De pago | No evaluadas en detalle. Opción si se necesita fiabilidad contractual |
| Betfair Historical Data | Mercados del exchange | — | Sí | De pago | Única vía para backtestear CLV/ROI sobre el pasado (ver R4) |

### Formato de TennisMyLife

Columnas **idénticas al formato Sackmann más una añadida**, `indoor`:

```
tourney_id, tourney_name, surface, draw_size, tourney_level, indoor, tourney_date,
match_num, winner_id, winner_seed, winner_entry, winner_name, winner_hand, winner_ht,
winner_ioc, winner_age, winner_rank, winner_rank_points, loser_* (idem), score, best_of,
round, minutes, w_ace, w_df, w_svpt, w_1stIn, w_1stWon, w_2ndWon, w_SvGms, w_bpSaved,
w_bpFaced, l_* (idem)
```

`indoor` es relevante: el brief la pide como feature y el formato Sackmann original **no la
tiene** (habría que derivarla por torneo, con error). Aquí viene dada.

### Riesgos de las fuentes

1. **Conflicto de licencias en TennisMyLife.** El sitio declara MIT (permite uso comercial),
   pero también admite que los datos se han "enriquecido" desde terceros y que los CSV de WTA
   se añadieron "usando CSVs ya disponibles online". Si una parte deriva de Sackmann
   (CC BY-NC-SA, con cláusula *ShareAlike*), **no podría relicenciarse bajo MIT**.
   *Postura adoptada:* tratar el conjunto como **no comercial**, que es la restricción más
   estricta de las aplicables. Coherente con que EdgeCourt es investigación en paper betting.
2. **Procedencia mixta y no auditable.** "Periódicos y blogs de tenis" no es una cadena de
   custodia verificable. Obliga a validación propia.
3. **Mirrors congelados.** Sirven como referencia histórica, nunca como fuente operativa.
4. **Punto único de fallo.** Si TennisMyLife cae, no hay fuente viva gratuita de reemplazo
   inmediata. Los mirrors cubren el histórico, no los partidos nuevos.

### Estrategia adoptada

- **Fuente primaria: TennisMyLife**, por ser la única viva con estadísticas por partido, y
  porque incluye `indoor`. Sin una fuente viva, el sistema no puede predecir partidos futuros,
  que es justamente su propósito.
- **Fuente de contraste: el mirror `Aneeshers/tennis-sackmann-archive`**. No se usa para
  entrenar: se usa para **auditar** el solapamiento temporal con TennisMyLife y cuantificar
  discrepancias. Dos fuentes independientes que coinciden es la única verificación de
  integridad disponible cuando el original ha desaparecido.
- **Restricción de uso: no comercial**, con atribución a Jeff Sackmann y a TennisMyLife.

### Validaciones de integridad obligatorias en la ingesta

Porque no hay original contra el que contrastar:

- continuidad temporal: partidos por año sin huecos ni saltos inexplicables;
- coherencia de rankings: rango plausible, evolución continua por jugador;
- coherencia aritmética de las estadísticas: `1stIn ≤ svpt`, `1stWon ≤ 1stIn`,
  `bpSaved ≤ bpFaced`, `aces ≤ svpt`;
- coherencia entre `score` y `sets`/`minutes`;
- identificadores de jugador estables (un `player_id` no cambia de nombre);
- duplicados exactos y casi-exactos;
- discrepancias frente al mirror en el periodo solapado, cuantificadas y reportadas.

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

## Resultado de la ingesta (2026-09-18)

Ejecutado con `edgecourt data fetch --from-year 1991` + `edgecourt data import --from-year 1991`.

| Métrica | Valor |
|---|---|
| Partidos en `match_facts` | **113.544** |
| Cobertura temporal | 1990–2026 (los torneos de 1991 que arrancan en dic-1990 arrastran esa fecha) |
| `P(target=1)` | **0.5004** — la aleatorización A/B funciona |
| Con estadísticas de partido | 91,1 % |
| Con ranking de ambos jugadores | 97,4 % |
| Con `indoor` | 91,5 % |
| Superficies | hard 57.549 · clay 37.583 · grass 11.137 · carpet 7.136 · nulo 139 |
| Finalización | completed 109.896 · retired 3.071 · walkover 546 · defaulted 29 |
| Duplicados reales eliminados | 11 |

### Integridad: contraste contra la fuente de referencia

| Métrica | Valor |
|---|---|
| Partidos emparejados | 101.025 |
| Tasa de emparejamiento (nombre exacto) | 88,99 % |
| Tasa con nombres compactados | 91,01 % |
| **Acuerdo en el ganador** | **100,00 %** |

La cifra que importa es la última: en los 101.025 partidos que ambas fuentes contienen,
**coinciden en el ganador sin una sola excepción**. Es la evidencia más fuerte de integridad
disponible desde que el original desapareció.

El ~10 % no emparejado **no** es ausencia de datos, sino variación de nomenclatura entre
fuentes. Comprobado sobre 2023: de 441 nombres normalizados, solo 26 difieren, y las
diferencias son de espaciado o transliteración —`albert ramosvinolas` / `albert ramos`,
`chunhsin tseng` / `chun hsin tseng`, `chung seong yun` / `chung yunseong`—. Esos pocos
jugadores generan el 10 % de partidos sin emparejar. El déficit es de la normalización, no de
la cobertura.

**Este hallazgo anticipa PHASE 8b:** el mismo problema, amplificado, aparecerá al emparejar
jugadores con los mercados de Betfair. Confirma que merece una fase propia y que el
emparejamiento difuso silencioso es inaceptable.

### Anomalías detectadas por las validaciones

Sobre 113.544 partidos, ~90 filas (0,08 %) presentan incoherencias aritméticas reales de la
fuente: `first_won > first_in`, `bp_saved > bp_faced`, `aces > svpt`, o un ganador con menos
sets que el perdedor. Son errores de la fuente, no del importador: se cuentan, se reportan en
`data/results/ingest_report.json` y quedan disponibles para excluirse en el entrenamiento.

## Split temporal (fijado el 2026-09-18, en adelante inmutable)

| Conjunto | Años | Partidos aprox. | Uso |
|---|---|---|---|
| TRAIN | 2000–2022 | ~70.000 | entrenamiento |
| VALIDATION | 2023 | ~2.900 | **toda** la iteración de desarrollo y ajuste |
| TEST | 2024–2025 | ~6.000 | evaluación final, consultas contadas y registradas |
| LIVE | 2026– | creciente | out-of-sample continuo, nunca se entrena con él |

Se descarta 1991–1999 del entrenamiento por defecto: el tenis de esa época (superficie carpet,
raquetas y calendario distintos) tiene un régimen suficientemente diferente como para que su
aportación sea dudosa. Los datos se conservan y el rango es configurable, de modo que la
decisión se pueda revisar con evidencia en lugar de por intuición.

El uso repetido del TEST para tomar decisiones es leakage humano y produce optimismo
sistemático. El desarrollo usa **solo** VALIDATION, y cada evaluación sobre TEST se registra en
`data/results/test_evaluations.jsonl` con fecha, modelo y motivo.

## Almacenamiento

- `data/raw/` — ficheros originales tal cual llegaron, nunca modificados.
- `data/processed/matches/` — `match_facts` en Parquet particionado por año.
- `data/odds/` — snapshots de Betfair, particionados por fecha.
- `data/results/` — métricas, informes y registro de evaluaciones.

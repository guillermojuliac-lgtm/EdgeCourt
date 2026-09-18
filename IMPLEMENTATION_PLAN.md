# EdgeCourt — Implementation Plan

Documento vivo. Cada fase es pequeña, verificable y tiene criterio de aceptación explícito.
No se avanza de fase si los tests críticos de esa fase fallan.

**Estado: PHASE 0 ✅ · PHASE 1 ✅ · PHASE 2 ✅ · PHASE 3 ✅ · siguiente: PHASE 8 (collector Betfair).**

**Orden de fases revisado (2026-09-18):** el collector de Betfair (PHASE 8) se adelanta a
continuación de PHASE 3. Cada semana sin recolectar es muestra perdida que no se recupera,
y sin histórico del exchange (R4) la validación económica solo puede ser hacia delante.
El orden efectivo pasa a ser: 0 → 1 → 2 → 3 → **8 → 8b** → 4 → 5 → 6 → 7 → 9 → 10 → … → 15.

**Pregunta de investigación (única métrica de éxito):**
> ¿Podemos generar probabilidades de tenis suficientemente buenas como para identificar
> de manera consistente situaciones donde el precio de Betfair ofrece esperanza matemática
> positiva después de costes?

El objetivo del proyecto es **intentar refutar** esta hipótesis, no confirmarla.

---

## 0. Entorno detectado (2026-09-17)

| Elemento | Valor |
|---|---|
| OS | Ubuntu 26.04.1 LTS |
| CPU / RAM / disco | 16 cores / 15 GiB / 369 GiB libres |
| Python del sistema | 3.14.4 |
| `uv` | **no instalado** |
| `pip` | no disponible en el intérprete del sistema |
| Red | PyPI y astral.sh accesibles |
| Repo git | limpio, commit inicial, solo `README.md` |

**Implicación:** `uv` se instalará en modo usuario (`~/.local/bin`, sin `sudo`) y gestionará
un Python 3.13 propio del proyecto. No se toca el Python del sistema.

---

## 1. Decisiones de diseño (y desviaciones justificadas del brief)

Todas las desviaciones van en la dirección de *menos* piezas, no de más.

| # | Decisión | Justificación |
|---|---|---|
| D1 | **Python 3.13 gestionado por `uv`**, no el 3.14 del sistema | XGBoost / scikit-learn / pyarrow tienen wheels maduras para 3.13; en 3.14 el riesgo de compilar desde fuente es real. Reproducibilidad > novedad. |
| D2 | **Parquet es el almacén canónico; DuckDB es el motor de consulta**, no un servidor de estado | Evita un fichero `.db` mutable como fuente de verdad. DuckDB lee Parquet/JSONL directamente. Menos estado, más reproducible. `database.py` → `storage.py`. |
| D3 | **Paper ledger = JSONL append-only con hash encadenado**, no tabla mutable | La inmutabilidad es un requisito duro (§15). Un fichero append-only con `prev_hash` hace que una modificación retrospectiva sea *detectable*, no solo desaconsejada. DuckDB lo consulta igual. |
| D4 | **CLI con `argparse`**, no `typer`/`click` | Subcomandos anidados son suficientes con stdlib. Una dependencia menos en la ruta crítica. |
| D5 | **Logging: `logging` stdlib + formatter JSON propio + `RotatingFileHandler`** | `structlog` no aporta lo bastante para justificarse. Rotación ya está en stdlib. |
| D6 | **Telegram vía `httpx` directo** (2 endpoints) | `python-telegram-bot` es una dependencia grande para `sendMessage`. |
| D7 | La decisión `betfairlightweight` vs `httpx` se pospone a **PHASE 8** | Depende de si usamos login por certificado (no interactivo, requerido para 24/7). Se documentará la elección en `docs/ARCHITECTURE.md` cuando se tome, no antes. |
| D8 | **`models/` y `data/` fuera de git** (solo `.gitkeep`) | §26. Los modelos se versionan por manifiesto JSON + hash, no por binario en git. |
| D9 | **Un único proceso `predictor` con scheduler interno**, no varios daemons, hasta que haya motivo | En PHASE 15 se evalúa si `collector` y `predictor` deben separarse de verdad. |
| D10 | **Sin dependencia de datos de Betfair históricos** para PHASES 1–7 | Ver R4. La validación de esas fases es puramente probabilística (Brier/LogLoss/calibración), no económica. |
| D11 | **TennisMyLife como fuente primaria; mirror de Sackmann solo como contraste** | Los repos originales de Sackmann desaparecieron (verificado 2026-09-18). TML es la única fuente viva con estadísticas por partido y además aporta `indoor`. El mirror no entrena: audita. Ver `docs/DATA.md`. |
| D12 | **`match_id` como hash de contenido**, no `tourney_id + match_num` | `match_num` está vacío en cientos de partidos reales de la fuente, lo que colapsaba esos partidos en un id nulo y los eliminaba al deduplicar. |

---

## 2. Riesgos detectados (revisión crítica del plan)

Ordenados por impacto sobre la validez del resultado, no por dificultad técnica.

### R1 — Data leakage por estadísticas del propio partido — **CRÍTICO**
Los datasets históricos de tenis (p. ej. formato Sackmann) incluyen `w_ace`, `w_df`, `w_1stWon`…
que son **estadísticas del partido que queremos predecir**. Usarlas como feature produce
un modelo con AUC ~0.99 y valor predictivo real cero.

*Mitigación:* toda estadística de rendimiento entra al modelo **únicamente** como agregado
histórico de partidos estrictamente anteriores (`shift(1)` por jugador, orden cronológico).
Se codifica una barrera arquitectónica: las columnas crudas del partido viven en el dataframe
`match_facts` y el generador de features **no recibe acceso** a ellas salvo por la ruta de
agregación temporal. `test_no_future_data_leakage` lo verifica con un test de envenenamiento
(se corrompe el resultado del partido T y se comprueba que las features de T no cambian).

### R2 — Leakage sutil: orden de empate y Elo — **ALTO**
Dos partidos del mismo jugador en la misma fecha (habitual en dobles, qualy, o fechas
imprecisas) pueden ordenarse de forma que el partido A use el Elo posterior a B.
*Mitigación:* clave de orden determinista `(date, tourney_id, round_order, match_num)`;
el Elo se actualiza en una única pasada secuencial; `test_elo_is_chronological` verifica
que `elo_before` de cada partido solo depende de partidos con clave de orden menor.

### R3 — Sesgo de selección A/B — **ALTO**
Si "jugador A" es siempre el ganador (como en los datasets crudos, que traen `winner_`/`loser_`),
el target es trivialmente 1 y el modelo aprende el orden de columnas.
*Mitigación:* aleatorización determinista (hash del `match_id`) de la asignación A/B en la
construcción del dataset, antes de cualquier feature. Test: `P(target=1) ≈ 0.5 ± tolerancia`.

### R4 — No existe histórico de cuotas Betfair — **CRÍTICO para la conclusión**
No disponemos de odds históricas de Betfair. Consecuencia dura y no negociable:
**no se puede backtestear ROI ni CLV sobre 2018–2024.** El backtest temporal (PHASE 7)
solo puede validar *calidad probabilística*. La validación económica empieza en PHASE 11
y es **forward-only**, en tiempo real, acumulando muestra desde cero.
*Mitigación:* el collector (PHASE 8) se pone en marcha **lo antes posible** y en paralelo a
las fases de modelado, porque su valor es el tiempo acumulado. Se considerará (documentado,
no automatizado) si merece la pena adquirir histórico comercial.

### R5 — Emparejamiento de nombres de jugador (Sackmann ↔ Betfair) — **ALTO**
`Novak Djokovic` vs `Djokovic N.` vs `N. Djokovic`; homónimos; transliteraciones.
Un fallo aquí no produce un error visible, produce predicciones aplicadas al jugador
equivocado. Merece **fase propia (PHASE 8b)** con tabla de mapeo persistente, revisión
manual de dudosos y **fallo explícito (nunca fuzzy-match silencioso)** cuando la confianza
es baja. Un partido sin mapeo confiable se descarta, no se adivina.

### R13 — Sobreconfianza del modelo confundida con value — **ALTO** *(nuevo, 2026-09-18)*
Medido en PHASE 2: el Elo tiene un ECE de ~0,055, mientras que el umbral de edge por defecto
es 0,03. Un modelo mal calibrado genera "value" aparente justo donde su sesgo es mayor —en
los favoritos claros—, y esas apuestas parecerían las más atractivas.
*Mitigación:* ningún modelo alimenta al Value Engine sin pasar por calibración (PHASE 6), y el
ECE out-of-sample se convierte en requisito de entrada, no en una métrica informativa más.

### R12 — Fuente de datos única y frágil — **ALTO** *(nuevo, 2026-09-18)*
La fuente de referencia del sector (repos de Jeff Sackmann) **desapareció**. La sustituta
viva, TennisMyLife, es un proyecto pequeño: si cae, no hay reemplazo gratuito inmediato, y
su procedencia ("periódicos y blogs de tenis") no es auditable.
*Mitigación:* los CSV crudos se conservan en `data/raw/` con manifiesto y SHA-256, de modo
que el histórico ya descargado sobrevive a la caída del origen; contraste sistemático contra
el mirror archivístico; validaciones aritméticas propias en cada ingesta. Verificado: 100 %
de acuerdo en el ganador sobre 101.025 partidos contrastados.

### R6 — Tamaño de muestra necesario — **ESTRUCTURAL**
Con stake plano y cuotas medias ~2.0, la desviación típica del retorno por apuesta es ≈1.0 u.
Detectar un yield real de +3% con potencia ~80% y α=5% requiere del orden de
**n ≈ (2.8 / 0.03)² ≈ 8.700 apuestas liquidadas**. A 6 apuestas/día son ~4 años.
*Implicación honesta:* el ROI paper **no** será concluyente a corto plazo. Por eso **CLV**
(mucho más eficiente estadísticamente, señal por apuesta con menos ruido) y la **calibración**
(usa las 142 predicciones diarias, no las 6 apuestas) son las métricas primarias.
Esto se documenta en `docs/METRICS.md` y se recuerda en cada informe.

### R7 — Ilusión de edge por comisión y spread ignorados — **ALTO**
Un edge de +5% desaparece con 2–5% de comisión sobre ganancias netas + spread back/lay.
*Mitigación:* el Value Engine reporta siempre `expected_value_after_costs` como cifra
principal; `expected_value` bruto es informativo. Umbral mínimo se aplica **sobre el neto**.

### R8 — Sobreajuste por iteración humana sobre el test set — **MEDIO-ALTO**
Mirar el test de 2024 repetidas veces y ajustar es leakage humano.
*Mitigación:* split fijado en PHASE 1 y escrito en `docs/DATA.md`. El año TEST se consulta
un número **limitado y registrado** de veces (log de evaluaciones en `data/results/`).
El desarrollo iterativo usa exclusivamente VALIDATION.

### R9 — Retiros, walkovers y partidos incompletos — **MEDIO**
Un `RET` en el primer set no es una victoria predecible y contamina tanto el Elo como el target.
*Mitigación:* flag `completion_status`; política documentada (por defecto: se excluyen de
train y de las métricas; se registran aparte). Betfair además anula ciertos mercados por RET,
lo que debe reflejarse en el ledger.

### R10 — Ruta accidental a apuestas reales — **CRÍTICO (seguridad)**
*Mitigación:* en esta versión **no existe** ninguna función que envíe una orden. La capa
Betfair es de solo lectura por construcción. Además, `BETTING_MODE` solo admite `paper`
(validación Pydantic que **rechaza** cualquier otro valor) y hay un test que falla si el
código base contiene llamadas a endpoints de apuesta (`placeOrders`).

### R11 — Deriva temporal del mercado (el mercado mejora) — **MEDIO**
Un edge de 2019 puede no existir en 2026.
*Mitigación:* todas las métricas se reportan segmentadas por mes; se vigila estabilidad, no
solo el agregado.

---

## 3. Arquitectura de flujo

```
CSV/Parquet histórico ──► ingest ──► match_facts (Parquet, inmutable)
                                          │
                                          ▼
                           Elo secuencial (cronológico)
                                          │
                                          ▼
                       feature builder (solo datos < T)  ◄── barrera anti-leakage
                                          │
                                          ▼
                     probability model (Elo | LogReg | XGB)
                                          │
                                          ▼
                              calibration (sigmoid/isotonic)
                                          │
        Betfair snapshots ──► market probability (back/lay/spread/comisión)
                                          │
                                          ▼
                                    VALUE ENGINE   (edge, EV, EV neto)
                                          │
                                          ▼
                                    RISK ENGINE    (filtros, stake, caps)
                                          │
                                          ▼
                              PAPER LEDGER (append-only, hash chain)
                                          │
                                          ▼
                         settlement ──► METRICS (Brier, CLV, ROI, DD)
```

El modelo **nunca** decide stake. El Value Engine **nunca** decide apostar. Solo el Risk
Engine produce un stake, y solo el Paper Ledger lo registra.

---

## 4. Fases

Formato: **Entregable** → **Tests** → **Criterio de aceptación**.

### PHASE 0 — Bootstrap, configuración y andamiaje de tests ✅
- `uv` en modo usuario + Python 3.13 del proyecto; `pyproject.toml`; venv.
- Estructura de paquetes `src/edgecourt/` (§3), `.gitignore`, `.env.example`, `README` inicial.
- `config.py` con Pydantic Settings tipado; `BETTING_MODE` restringido a `paper`.
- `logging_setup.py`: JSON estructurado, rotación, ficheros separados, redacción de secretos.
- `storage.py`: helpers Parquet + conexión DuckDB read-only sobre Parquet.
- CLI esqueleto: `edgecourt status`.
- **Tests:** `test_config` (rechaza `BETTING_MODE=live`), `test_no_real_betting_surface`
  (grep del árbol: sin `placeOrders`), `test_logging_redacts_secrets`, `test_storage_roundtrip`.
- **Aceptación:** `uv run pytest` verde, `uv run edgecourt status` imprime entorno y modo.

### PHASE 1 — Dataset histórico ✅
- Documentar fuentes en `docs/DATA.md` (licencia, cobertura, campos, limitaciones).
  **No se descarga nada sin confirmación explícita del usuario.**
- `data/ingest.py`: importa CSV/Parquet → esquema canónico `match_facts` validado (Pydantic
  o validación explícita de esquema), Parquet particionado por año.
- Aleatorización determinista A/B (R3); `completion_status` (R9); clave de orden (R2).
- Split temporal fijo escrito en `docs/DATA.md` (R8).
- **Tests:** `test_schema_validation`, `test_ab_randomization_balance`, `test_ordering_key_is_total`,
  `test_no_duplicate_match_ids`.
- **Aceptación:** dataset canónico materializado; informe de cobertura (partidos/año, % nulos por campo).
- **Resultado:** 113.544 partidos (1990–2026), `P(target=1)=0.5004`, 6,9 MB en Parquet.
  Contraste contra el mirror: **100 % de acuerdo en el ganador** sobre 101.025 partidos.
  Detalle en `docs/DATA.md`.

### PHASE 2 — Elo y Surface Elo ✅
- Elo global + hard/clay/grass, una pasada cronológica, K configurable.
- Persistencia de `elo_*_before` por partido y snapshot de ratings finales.
- **Tests:** `test_elo_is_chronological`, `test_elo_zero_sum`, `test_elo_no_future_data`,
  `test_surface_elo_isolated`.
- **Aceptación:** Elo supera claramente a la moneda y al ranking ATP puro en el VALIDATION set
  (Brier y LogLoss reportados). Es nuestro benchmark base.
- **Resultado:** Brier skill vs moneda **+9,9 % (VAL 2023)** y **+11,3 % (TEST 2024-25)**, estable.
  Mejor variante: mezcla 50/50 global+superficie. Tests bloqueantes en verde.
- **Hallazgo:** el Elo está **sistemáticamente sobreconfiado** (ECE ≈ 0,055, del mismo orden que
  el edge que se busca). No es utilizable como generador de probabilidades para el Value Engine
  sin calibrar: eleva la prioridad de PHASE 6. Detalle en `docs/MODELS.md`.

### PHASE 3 — Feature engineering ✅
- Features de §7, todas relativas A−B, todas construidas con `shift` estricto (R1).
- Cada feature documentada en `docs/MODELS.md`: definición, ventana, justificación, nulos.
- **Tests (máxima prioridad):** `test_no_future_data_leakage` (test de envenenamiento),
  `test_feature_generation`, `test_features_antisymmetry` (invertir A/B invierte el signo),
  `test_null_policy`.
- **Aceptación:** los 4 tests verdes. Un fallo aquí **bloquea todas las fases siguientes**.
- **Resultado:** 21 features + 5 de contexto sobre 113.544 partidos. Máximo AUC univariante
  **0,7298** (`elo_diff`), sin ninguna señal de leakage. Control positivo: las mismas métricas
  tomadas del propio partido darían AUC 0,9171 — la diferencia mide el tamaño de la trampa
  evitada. Todas las features con media ≈ 0 (antisimetría confirmada). Ver `docs/MODELS.md`.

### PHASE 4 — Logistic Regression baseline
- Pipeline sklearn (imputación + escalado + LogReg), entrenado solo con TRAIN.
- **Tests:** `test_probability_sum` (P(A)+P(B)=1), `test_model_versioning` (manifiesto + hash).
- **Aceptación:** Brier/LogLoss/AUC/calibration error frente a Elo en VALIDATION.
  Si no mejora a Elo, se reporta como tal — no se fuerza.

### PHASE 5 — XGBoost
- Mismo contrato de entrada/salida que LogReg (intercambiables).
- Hiperparámetros por búsqueda **solo sobre VALIDATION**, con presupuesto de iteraciones registrado.
- **Aceptación:** comparación honesta en la misma tabla que Elo y LogReg.

### PHASE 6 — Calibración
- Sigmoid/Platt e isotónica sobre un *calibration set* temporal separado (nunca el de train).
- Curvas de calibración y ECE por decil; almacenadas como artefacto.
- **Tests:** `test_calibration_improves_or_neutral`, `test_calibrated_probabilities_valid`.
- **Aceptación:** ECE reducido sin degradar LogLoss.

### PHASE 7 — Backtest temporal / walk-forward
- Walk-forward: reentrenar por ventana anual, evaluar el año siguiente.
- **Solo métricas probabilísticas** (R4). No se reporta ROI aquí, ni siquiera simulado, para
  no crear una falsa sensación de rentabilidad.
- **Tests:** `test_walkforward_windows_disjoint`, `test_no_train_after_eval_date`.
- **Aceptación:** estabilidad año a año del Brier skill score frente a Elo.

### PHASE 8 — Betfair market data (solo lectura) + collector ⏩ *(adelantada: va tras PHASE 3)*
- Autenticación (cert o interactiva, decisión D7), listado de eventos/mercados de tenis,
  back/lay/liquidez/timestamp, snapshots a 24h/12h/6h/1h/10m/cierre (best-effort).
- Snapshots en Parquet particionado por fecha; timestamp real de observación, nunca el teórico.
- Retry con backoff exponencial, timeouts, apagado limpio por SIGTERM.
- **Prioridad de calendario:** empezar a recolectar cuanto antes (R4), aunque el modelo aún
  no esté listo. El tiempo es el recurso escaso.
- **Tests:** `test_snapshot_schema`, `test_backoff_policy`, `test_no_order_endpoints` (R10),
  `test_graceful_shutdown`.

### PHASE 8b — Emparejamiento de entidades (R5)
- Tabla de mapeo persistente jugador/torneo entre fuente histórica y Betfair.
- Normalización determinista + candidatos dudosos a revisión manual; **sin fuzzy silencioso**.
- **Tests:** `test_name_normalisation`, `test_ambiguous_match_is_rejected`.
- **Aceptación:** tasa de emparejamiento medida y publicada; los no emparejados se descartan.

### PHASE 9 — Value Engine
- `edge_probability`, `expected_value`, `expected_value_after_costs` (comisión + spread).
- Distinción documentada entre probabilidad implícita bruta, desvigada y ajustada por lado.
- **Tests:** `test_market_probability`, `test_expected_value`, `test_overround_removal`,
  `test_costs_reduce_ev`.

### PHASE 10 — Risk Engine
- Flat staking; Kelly fraccional opcional (≤0.25 por defecto) con hard caps independientes.
- Límites: exposición diaria, exposición por mercado, edge mínimo, liquidez mínima, drawdown máx.
- **Tests:** `test_flat_staking`, `test_fractional_kelly_caps`, `test_drawdown`,
  `test_exposure_limits`, `test_kelly_never_full`.

### PHASE 11 — Paper betting
- Ledger JSONL append-only con `prev_hash` (D3); campos de §15; `model_version` obligatorio.
- Liquidación separada en el tiempo del registro; nunca reescritura.
- **Tests:** `test_paper_ledger_immutability`, `test_hash_chain_detects_tampering`,
  `test_settlement_does_not_mutate_prediction`.

### PHASE 12 — CLV
- `entry_odds`/`closing_odds` → CLV en espacio de probabilidad desvigada (definición documentada
  y única). Métrica primaria junto a calibración (R6).
- **Tests:** `test_clv_definition`, `test_clv_sign_convention`.

### PHASE 13 — Production vs Challenger
- Dos slots; ambos predicen; solo Production decide. Predicciones paralelas persistidas.
- Comparador → recomendación `KEEP_PRODUCTION` / `REVIEW_CHALLENGER`. **Nunca promoción automática.**
- **Tests:** `test_challenger_cannot_place_paper_bets`, `test_comparison_report`.

### PHASE 14 — Reentrenamiento automatizado
- Pipeline semanal: datos nuevos → validación → features → train challenger → calibrar →
  backtest temporal → comparar → informe. Sin online learning.
- **Tests:** `test_retraining_pipeline_idempotent`, `test_new_model_lands_in_challenger_slot`.

### PHASE 15 — systemd + Telegram
- Ficheros `.service`/`.timer` de ejemplo **mostrados, no instalados**.
- Telegram: solo eventos relevantes (§21), sin spam por snapshot.
- **Tests:** `test_notification_throttling`, `test_no_secrets_in_messages`.

---

## 5. Simplificaciones aplicadas tras revisar el plan

1. `database.py` eliminado → `storage.py` (D2).
2. Sin dependencia de CLI de terceros (D4) ni de logging de terceros (D5) ni de cliente
   Telegram (D6): 3 dependencias menos.
3. PHASE 7 **no** produce ROI simulado: elimina una pieza de código y, sobre todo, elimina
   una fuente de autoengaño (R4).
4. PHASE 8b se separa de PHASE 8 porque el emparejamiento de nombres es un riesgo propio (R5),
   no un detalle de implementación del collector.
5. Un solo proceso de larga duración hasta que se demuestre necesidad de dos (D9).

## 6. Criterio de decisión final (definido *antes* de ver resultados)

EdgeCourt se considerará **evidencia a favor** de la hipótesis solo si, de forma conjunta:

- calibración out-of-sample buena (ECE bajo, curva cercana a la diagonal) **y**
- Brier skill score positivo y estable frente al mercado en el subconjunto apostable **y**
- **CLV medio positivo con significancia estadística** sobre una muestra suficiente **y**
- ROI paper después de costes no negativo, con drawdown tolerable **y**
- estabilidad mes a mes, sin que el resultado dependa de un puñado de apuestas.

Cualquier resultado positivo que dependa de un segmento pequeño, de un mes concreto o de
cuotas extremas se reportará como **no concluyente**. Los segmentos negativos se publican.

# Roadmap

Reconstruido el **2026-09-27** a partir del código, los commits, la base de datos (solo
lectura) y la documentación existente. Solo se marca como hecho lo que se ha podido verificar.

> **Dos numeraciones.** [`IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) usa la numeración
> original, con 16 fases (PHASE 0–15, más 8b). Este roadmap usa una numeración **consolidada**,
> más gruesa, e introduce la **Phase 3.5**. La tabla de equivalencias está al final. En caso de
> duda, el detalle técnico de cada fase del plan sigue siendo la referencia; el estado vigente
> es el de este documento.

## Resumen

| Phase | Nombre | Estado |
|---|---|---|
| 0 | Bootstrap, configuración y tests | ✅ COMPLETADO |
| 1 | Data ingestion / canonical dataset | ✅ COMPLETADO |
| 2 | Elo / Surface Elo | ✅ COMPLETADO |
| 3 | Feature engineering | ✅ COMPLETADO |
| **3.5** | **Market validation / Betfair quality** | 🔶 **EN CURSO** |
| 4 | Probability models | ◐ PARCIAL (LogReg hecha) · 🚫 resto BLOQUEADO por DEC-015 |
| 5 | Calibration / Value Engine | 🚫 BLOQUEADO / requiere validación |
| 6 | Risk Engine / Paper betting | ⏳ PENDIENTE (depende de 5) |
| 7 | Evaluation (walk-forward, CLV, P vs C) | 🚫 BLOQUEADO (el CLV depende de 3.5-B) |
| 8 | Betfair collector (solo lectura) | ✅ COMPLETADO y en operación (defectos derivados a 3.5) |
| 8b | Identity resolution | ⏳ PENDIENTE |
| — | Operación: reentrenamiento, Telegram, predictor | ⏳ PENDIENTE |

Orden efectivo: 0 → 1 → 2 → 3 → 8 → 4 (LogReg) → **3.5** → 8b → 4 (resto) → 5 → 6 → 7.
El collector (8) se adelantó el 2026-09-18 porque cada semana sin recolectar es muestra perdida
(riesgo R4). La Phase 3.5 se abrió el 2026-09-27, tras la auditoría de la Semana 1.

---

## ✅ COMPLETADO

### Phase 0 — Bootstrap
- **Qué incluye:** `uv` + Python 3.13, `config.py` tipado (`BETTING_MODE` = `paper`), logging
  JSON con redacción de secretos, `storage.py` y CLI `argparse`.
- **Commit:** `d8f0885` (2026-09-17).
- **Detalle:** `IMPLEMENTATION_PLAN.md` §4.

### Phase 1 — Data ingestion / canonical dataset
- **Resultado:**
  - `match_facts` con **113.544 partidos ATP** (1990–2026) y `P(target=1)` = 0,5004.
  - Contraste con el mirror: **100 % de acuerdo en el ganador** sobre 101.025 partidos.
  - Split temporal fijado.
- **Commit:** `ed7cb77` (2026-09-18).
- **Detalle:** [`DATA.md`](DATA.md).

### Phase 2 — Elo / Surface Elo
- **Resultado:**
  - Brier skill frente a la moneda: +9,9 % en VAL 2023 y +11,3 % en TEST 2024–25.
  - **Hallazgo:** el Elo está sobreconfiado (ECE ≈ 0,055), riesgo R13.
- **Commit:** `c3fae0a` (2026-09-18).
- **Detalle:** [`MODELS.md`](MODELS.md).

### Phase 3 — Feature engineering
- **Resultado:** 21 features antisimétricas + 5 de contexto. El AUC univariante máximo es 0,7298,
  sin señales de leakage (hay control positivo).
- **Commit:** `6439bcb` (2026-09-18).
- **Detalle:** [`MODELS.md`](MODELS.md).

### Phase 8 — Betfair collector (solo lectura)
- **Resultado:**
  - Cliente `httpx` con 3 operaciones de lectura y login por certificado con jurisdicción `es`.
  - Persistencia en PostgreSQL con captura híbrida.
  - Servicio systemd **en operación desde el 2026-09-18 a las 16:00 CEST**.
  - En la Semana 1: 1.497 observaciones, 62 mercados y 100 % de uptime.
- **Commits:** `ba92168`, `dbd7a9f`, `46fae68`, `025bab2`, `7eea268`, `34ac7a6`, `9bfebca`,
  `c292cbd`.
- **Detalle:** [`architecture/BETFAIR_COLLECTOR.md`](architecture/BETFAIR_COLLECTOR.md) y
  [`architecture/POSTGRESQL.md`](architecture/POSTGRESQL.md).
- **Defectos conocidos**, derivados a la Phase 3.5:
  - `close` no es un cierre fiable (3.5-B);
  - el `keepAlive` es insuficiente (3.5-A).

---

## 🔶 EN CURSO

### Phase 3.5 — Market validation / Betfair quality
- **Objetivo:** determinar si el mercado accesible tiene calidad suficiente y corregir lo que
  impediría evaluar un modelo.
- **Líneas de trabajo:**
  - **3.5-A** keepAlive de la sesión;
  - **3.5-B** closing price fiable;
  - **3.5-C** ausencia de torneos ATP/WTA regulares;
  - **3.5-D** seguir recopilando datos.
- **Hecho:**
  - Auditoría Semana 1 (2.ª parte), [2026-09-week1-market-audit](audits/2026-09-week1-market-audit.md).
  - **3.5-C investigada**, [2026-09-atp-wta-catalogue](investigations/2026-09-atp-wta-catalogue.md):
    el catálogo `.es` no ofreció ATP regular. EdgeCourt no pierde mercados.
- **Bloqueo interno:** hace falta decidir la fuente de mercado y el alcance realista antes de
  priorizar 3.5-B.
- **Criterio de salida y detalle:** [`phases/PHASE_03_5_MARKET_VALIDATION.md`](phases/PHASE_03_5_MARKET_VALIDATION.md).

---

## ◐ PARCIAL / 🚫 BLOQUEADO / REQUIERE VALIDACIÓN

### Phase 4 — Probability models
- **Hecho (PHASE 4 del plan), regresión logística:**
  - Brier skill frente al Elo: +4,71 % en VAL y +3,60 % en TEST.
  - ECE en TEST: de 0,053 a 0,0135.
  - Modelos en `models/challenger/`; no hay ninguno en producción.
  - Commit `fa8f11e`.
- **Pendiente (PHASE 5 del plan), XGBoost:**
  - Mismo contrato que la LogReg.
  - Búsqueda de hiperparámetros solo sobre VALIDATION.
  - `xgboost` todavía no está en las dependencias.
- **Bloqueo:** [DEC-015](DECISIONS.md#dec-015). No se avanza hasta cerrar la Phase 3.5.

### Phase 5 — Calibration / Value Engine
- **Incluye:** calibración con Platt o isotónica sobre un conjunto temporal separado (PHASE 6 del
  plan), y Value Engine con edge, EV y EV neto de comisión y spread (PHASE 9 del plan).
- **Estado del código:** `src/edgecourt/calibration/` y `src/edgecourt/value/` están **vacíos**
  (solo `__init__.py`).
- **Requiere validación:**
  - que el spread del mercado permita cualquier edge útil (auditoría: spread del favorito con
    mediana del 13,2 %);
  - la definición del método de desvigado.

### Phase 7 — Evaluation
- **Incluye:** backtest walk-forward, solo con métricas probabilísticas (PHASE 7 del plan); CLV
  (PHASE 12); comparación Production vs Challenger (PHASE 13).
- **Estado del código:** `metrics/probabilistic.py` existe (Brier, LogLoss, AUC, ECE). No hay CLV,
  ni `backtest`, ni `metrics`, ni `model compare` contra el mercado. Los comandos pendientes de
  la CLI salen con código 3.
- **Bloqueo:** el CLV requiere el closing price fiable de 3.5-B.

---

## ⏳ PENDIENTE

### Phase 6 — Risk Engine / Paper betting
- **Incluye:**
  - Risk Engine: flat staking, Kelly fraccional ≤0,25, límites de exposición y drawdown (PHASE 10
    del plan).
  - Paper ledger inmutable (PHASE 11 del plan).
- **Estado:**
  - Los límites están validados en `config.py`.
  - Las tablas `paper_bet` y `bet_settlement` existen, son inmutables y están vacías.
  - `src/edgecourt/risk/` y `src/edgecourt/paper/` están **vacíos**.

### Phase 8b — Identity resolution
- **Incluye:** mapeo persistente entre los jugadores de Betfair y los del dataset. Normalización
  determinista, **nunca fuzzy silencioso**, y descarte de los partidos dudosos.
- **Estado:** `betfair_runner.player_id` existe (nullable, sin FK) y está a **NULL en todas las
  filas**. No hay código de emparejamiento.
- **Nota:** con el catálogo `.es` observado (Davis Cup y Laver Cup como únicos eventos
  masculinos), el volumen no justifica la fase por ahora. Depende de la decisión sobre la fuente
  de mercado.

### Operación (PHASES 13–15 del plan)
- Reentrenamiento semanal en el slot `challenger`.
- Proceso `predictor`.
- Telegram: `src/edgecourt/notifications/` está vacío.
- El collector ya está en systemd; lo demás está pendiente.

---

## Equivalencia de numeraciones

| Roadmap (este documento) | `IMPLEMENTATION_PLAN.md` | Estado del plan |
|---|---|---|
| Phase 0 | PHASE 0 | ✅ |
| Phase 1 | PHASE 1 | ✅ |
| Phase 2 | PHASE 2 | ✅ |
| Phase 3 | PHASE 3 | ✅ |
| **Phase 3.5** | *(no existe)* | — |
| Phase 4 | PHASE 4 (LogReg) + PHASE 5 (XGBoost) | 4 ✅ · 5 pendiente |
| Phase 5 | PHASE 6 (Calibración) + PHASE 9 (Value Engine) | pendiente |
| Phase 6 | PHASE 10 (Risk) + PHASE 11 (Paper) | pendiente |
| Phase 7 | PHASE 7 (Walk-forward) + PHASE 12 (CLV) + PHASE 13 (P vs C) | pendiente |
| Phase 8 | PHASE 8 | ✅ |
| Phase 8b | PHASE 8b | pendiente |
| Operación | PHASE 14 + PHASE 15 | parcial (systemd del collector hecho) |

La agrupación de las PHASES 5–13 del plan en las Phases 4–7 del roadmap se propuso al reconstruir
el roadmap y quedó **aprobada el 2026-09-27**.

**Regla de numeración:**
- Desde el 2026-09-27, la referencia principal es la numeración de este documento y de
  `PROJECT_STATUS.md`.
- **No se renumeran las fases históricas.** Los commits, `IMPLEMENTATION_PLAN.md` y los
  documentos antiguos conservan su numeración original; esta tabla es el único puente entre
  ambas.

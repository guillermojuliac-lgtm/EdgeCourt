# Auditoría Semana 1 — calidad del mercado Betfair y operación del collector

> **Snapshot inmutable.** Este documento refleja el conocimiento disponible el **2026-09-27**.
> No se reescribe: si datos posteriores contradicen algo, se documenta en una auditoría nueva
> que enlace a esta. Convención en [`docs/README.md`](../README.md).

| Campo | Valor |
|---|---|
| Fecha de la auditoría | 2026-09-27 |
| Fase | [Phase 3.5 — Market validation](../phases/PHASE_03_5_MARKET_VALIDATION.md) |
| Datos analizados | `market_observation` + `runner_price` hasta 2026-09-27 08:11:52 CEST |
| Logs analizados | journal de `edgecourt-collector` + `logs/collector.log`, ventana 2026-09-20 08:49 → 2026-09-27 08:49 CEST |
| Modo | **Solo lectura.** Sesión PostgreSQL con `default_transaction_read_only = on`; no se modificó ningún dato, configuración, esquema ni código |
| Scripts y salida completa | [`2026-09-week1-market-audit/`](2026-09-week1-market-audit/) |

**Alcance.** Es la *segunda parte* de la auditoría de la Semana 1: calidad real del mercado, no
solo presencia de liquidez. La primera parte se hizo en una sesión anterior y **sus resultados
no quedaron guardados en el repositorio**; no se reconstruyen aquí para no inventarlos.

---

## 0. Resumen ejecutivo

1. **Ahora mismo el mercado no es operable en la práctica.** Ninguna observación tiene un
   spread ≤2 %, ni en el peor runner ni en el favorito. El spread del favorito tiene una mediana
   de **13,2 %** (**16 ticks**) y **ningún** favorito cotiza a ≤3 ticks.
2. **Tener liquidez no significa tener calidad.** El 65,8 % de las observaciones supera
   `MINIMUM_LIQUIDITY` = 50, pero la profundidad al mejor precio (`top_depth`) tiene una mediana
   de **~67 €** y el volumen casado (`total_matched`) tiene mediana 0.
3. **Por mercado, el panorama es peor que por observación.** Solo el **43,5 %** de los mercados
   mostró algún precio. Los 26 mercados en modo adaptive producen el **92,1 %** de las
   observaciones.
4. **Acercarse al inicio no mejora la calidad.** La liquidez crece algo (ratio mediano ×1,48),
   pero el spread no se estrecha y apenas se casa volumen.
5. **La oferta de la muestra es atípica:** solo Davis Cup, Billie Jean King Cup y Laver Cup.
   **No apareció ningún torneo ATP ni WTA regular** en los 9 días.
6. **El collector es fiable:** 10.054 ciclos, 0 fallos y un hueco máximo de 63,8 s en 7 días.
   Hay **dos problemas** que afectan al futuro CLV: la caducidad de la sesión cada 20 min
   (benigna) y **la etiqueta `close`, que no es un precio de cierre fiable** (crítico).

> ⚠️ **Sesgo principal: NO interpretar 1.497 observaciones como 1.497 mercados
> independientes.** 26 mercados aportan el 92,1 % de las filas. Toda conclusión debe mirar
> primero las métricas **por mercado** (§3).

---

## 1. Dataset analizado

| Métrica | Valor |
|---|---|
| Observaciones (`market_observation`) | **1.497** |
| Filas de precio (`runner_price`) | 1.967 (en 996 observaciones) |
| Mercados en catálogo (`betfair_market`) | **65** |
| Mercados observados al menos una vez | **62** |
| Partidos únicos entre los observados | **60** (2 partidos se reabrieron con otro `market_id`) |
| Mercados con observaciones adaptive | **26** |
| Observaciones procedentes de esos 26 mercados | **1.378 (92,1 %)** |
| Primera / última observación | 2026-09-18 10:21 / 2026-09-27 08:11 CEST |
| `collector_run_id` distintos | 5 (uno es la importación desde Parquet del 18-sep, con 16 observaciones) |
| `MINIMUM_LIQUIDITY` efectivo (solo leído) | 50 |

**Observaciones por etiqueta:**

| `snapshot_label` | observaciones | mercados | `minutes_to_start` mín–máx |
|---|---|---|---|
| 24h | 3 | 3 | 1.412–1.485 |
| 12h | 29 | 29 | 699–750 |
| 6h | 37 | 37 | 341–380 |
| 1h | 53 | 53 | 62–68 |
| 10m | 58 | 58 | 7,0–13,0 |
| close | 58 | 58 | 1,5–4,0 |
| adaptive | 1.259 | 26 | 0,01–350 |

**Mercados del catálogo nunca observados (3):**
- `1.262904639` (BJK, 25-sep): estuvo en el catálogo de 10:33 a 10:45 y lo sustituyó
  `1.262905554`.
- `1.262997506` y `1.262997528` (Laver Cup): empiezan el 27-sep, después del corte de datos.

**Partidos con más de un `market_id`:** Cristina Bucsa v Linda Noskova y Jessica Bouzas Maneiro v
Karolina Muchova (ambos el 25-sep).

### Distribución por competición

| Competición | Mercados | Observaciones | Mercados con precio | Mercados adaptive | Liq. máx. mediana (€) | Spread mediano (%) |
|---|---|---|---|---|---|---|
| Davis Cup | 33 | 1.024 | 17 | 16 | 381,91 | 31,78 |
| Billie Jean King Cup | 20 | 273 | 4 | 4 | 0,00 | 64,69 |
| Laver Cup | 9 | 200 | 6 | 6 | 1.100,84 | 25,96 |

**Durante la muestra no apareció ningún torneo ATP ni WTA regular.** Solo competiciones por
equipos o de exhibición. La causa se investiga en [Phase 3.5-C](../phases/PHASE_03_5_MARKET_VALIDATION.md).

**Encaje con el alcance del modelo** (cálculo complementario, hecho al documentar el 2026-09-27
sobre los mismos datos). El dataset histórico es de individuales ATP. Si se separa por sexo
(Davis y Laver = masculino; BJK = femenino) y por individual/dobles (`/` en el nombre del evento):

| Segmento | Mercados | Observaciones | Con precio | Adaptive |
|---|---|---|---|---|
| Masculino individual | 33 | 1.149 | 21 | 20 |
| Masculino dobles | 9 | 75 | 2 | 2 |
| Femenino individual | 14 | 255 | 4 | 4 |
| Femenino dobles | 6 | 18 | 0 | 0 |

El collector captura todo `MATCH_ODDS` de tenis (incluidos WTA y dobles). Solo **33 mercados**
corresponden al tipo de partido que el modelo actual sabe predecir, y **21** de ellos mostraron
precio.

---

## 2. Definiciones

- **Liquidez** = `total_available`: suma de los tamaños disponibles en los niveles 1–3, tanto a
  favor (BACK) como en contra (LAY), de todos los runners. Es una medida **bruta**: en un mercado
  de 2 runners, un BACK sobre A y un LAY sobre B son casi la misma liquidez.
- **`top_depth`** = por runner, `min(back_size_1, lay_size_1)`; después, el mínimo entre runners.
  Es lo que se podría casar al mejor precio en ambos lados.
- **Spread** = `max_spread_pct`: `(lay_1 − back_1) / back_1 × 100` del **peor** runner. Solo
  existe si hay precio en ambos lados.
- **Spread del favorito**: el mismo cálculo, en el runner de menor `back_price_1`.
- **Ticks**: distancia entre `back_1` y `lay_1` en la escala oficial de precios de Betfair
  (1,01–2 de 0,01 en 0,01; 2–3 de 0,02; 3–4 de 0,05; 4–6 de 0,1; 6–10 de 0,2; …).
- **Book back / book lay**: `Σ 1/back_1` y `Σ 1/lay_1` sobre los 2 runners.

> **Advertencia.** Los umbrales porcentuales (≤1 %, ≤2 %) son casi inalcanzables para el runner de
> cuota alta por la propia escala de ticks: a cuota 4,0, un tick ya supone un 2,5 %. Por eso se
> añaden el spread del favorito y el spread en ticks.

Bandas adaptive (`minutes_to_start`): **A90-360**, **A30-90**, **A10-30** y **A0-10**.
Coinciden con los tramos de cadencia de 30, 10, 5 y 1 minuto.

---

## 3. Métricas por OBSERVACIÓN

### 3.1 Cobertura y liquidez

| grupo | mercados | obs | % precios | % liq | % spread 2 lados | % liq ≥50 | liq P50 incl. 0 | liq P25 | liq P50 | liq P75 | liq P90 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 24h | 3 | 3 | 0,0 | 0,0 | 0,0 | 0,0 | 0,00 | — | — | — | — |
| 12h | 29 | 29 | 6,9 | 6,9 | 3,4 | 3,4 | 0,00 | 152,11 | 291,68 | 431,25 | 514,99 |
| 6h | 37 | 37 | 29,7 | 29,7 | 21,6 | 24,3 | 0,00 | 215,13 | 415,21 | 526,33 | 1.101,77 |
| 1h | 53 | 53 | 32,1 | 32,1 | 32,1 | 32,1 | 0,00 | 736,02 | 1.075,02 | 1.282,44 | 1.500,38 |
| 10m | 58 | 58 | 37,9 | 37,9 | 29,3 | 37,9 | 0,00 | 310,37 | 714,17 | 1.132,45 | 1.558,44 |
| close | 58 | 58 | 41,4 | 41,4 | 36,2 | 41,4 | 0,00 | 434,82 | 785,28 | 1.190,99 | 1.478,68 |
| **adaptive** | 26 | 1.259 | 73,1 | 73,1 | 62,9 | 72,4 | 430,57 | 321,92 | 645,82 | 1.194,48 | 1.637,16 |
| A90-360 | 11 | 99 | 93,9 | 93,9 | 79,8 | 88,9 | 480,54 | 314,75 | 480,54 | 1.091,50 | 1.441,04 |
| A30-90 | 19 | 106 | 89,6 | 89,6 | 84,0 | 89,6 | 971,01 | 559,62 | 999,35 | 1.275,64 | 1.589,25 |
| A10-30 | 20 | 90 | 88,9 | 88,9 | 78,9 | 88,9 | 712,93 | 639,44 | 848,43 | 1.271,95 | 1.505,10 |
| A0-10 | 26 | 964 | 67,6 | 67,6 | 57,4 | 67,3 | 320,82 | 319,78 | 644,30 | 1.152,39 | 1.637,16 |
| todos los hitos | 62 | 238 | 31,9 | 31,9 | 26,9 | 30,7 | 0,00 | 320,69 | 725,23 | 1.155,24 | 1.468,45 |
| **TODAS** | 62 | 1.497 | 66,5 | 66,5 | 57,2 | 65,8 | 321,92 | 321,92 | 648,78 | 1.167,48 | 1.589,25 |

Los percentiles de liquidez P25–P90 se calculan solo sobre observaciones con liquidez > 0.
`% precios` y `% liq` coinciden siempre: con estos datos, tener precio implica tener tamaño.

### 3.2 Spread BACK/LAY (peor runner)

Solo observaciones con precio en ambos lados.

| grupo | n | P25 | P50 | P75 | P90 | ≤1 % | ≤2 % | ≤5 % | ≤10 % | >10 % | ticks P50 | ticks P90 | spread fav. P50 | book back P50 | book lay P50 | `top_depth` P50 (€) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 12h | 1 | 14,66 | 14,66 | 14,66 | 14,66 | 0 | 0 | 0 | 0 | 100 | 17 | 17 | 14,66 | — | — | 5,84 |
| 6h | 8 | 14,97 | 29,12 | 43,32 | 93,98 | 0 | 0 | 0 | 0 | 100 | 21 | 35 | 11,68 | 1,09 | 0,90 | 41,84 |
| 1h | 17 | 16,67 | 31,05 | 49,37 | 57,02 | 0 | 0 | 5,9 | 5,9 | 94,1 | 26 | 31 | 13,93 | 1,09 | 0,90 | 62,49 |
| 10m | 17 | 24,37 | 36,99 | 61,11 | 123,89 | 0 | 0 | 5,9 | 11,8 | 88,2 | 26 | 31,6 | 13,07 | 1,09 | 0,91 | 68,41 |
| close | 21 | 26,17 | 34,47 | 53,25 | 111,54 | 0 | 0 | 4,8 | 9,5 | 90,5 | 26 | 34 | 13,45 | 1,09 | 0,90 | 88,35 |
| **adaptive** | 792 | 24,75 | 34,47 | 55,56 | 126,17 | 0 | 0 | 2,4 | 7,4 | 92,6 | 25 | 34 | 13,22 | 1,09 | 0,90 | 66,68 |
| A90-360 | 79 | 13,04 | 20,00 | 46,88 | 76,74 | 0 | 0 | 2,5 | 5,1 | 94,9 | 17 | 33 | 11,88 | 1,09 | 0,93 | 37,20 |
| A30-90 | 89 | 21,31 | 30,00 | 49,37 | 61,11 | 0 | 0 | 6,7 | 9,0 | 91,0 | 24 | 31,2 | 13,45 | 1,09 | 0,91 | 61,18 |
| A10-30 | 71 | 23,19 | 27,66 | 51,15 | 66,23 | 0 | 0 | 5,6 | 11,3 | 88,7 | 26 | 30 | 13,45 | 1,09 | 0,91 | 76,74 |
| A0-10 | 553 | 26,17 | 42,42 | 59,46 | 203,03 | 0 | 0 | 1,3 | 7,1 | 92,9 | 25 | 37 | 13,22 | 1,09 | 0,90 | 66,68 |
| todos los hitos | 64 | 21,84 | 32,58 | 53,51 | 111,54 | 0 | 0 | 4,7 | 7,8 | 92,2 | 25,5 | 34 | 13,45 | 1,09 | 0,90 | 68,41 |
| **TODAS** | 856 | 24,66 | 34,47 | 55,56 | 121,74 | 0 | 0 | 2,6 | 7,5 | 92,5 | 25 | 34 | 13,22 | 1,09 | 0,90 | 66,68 |

El hito 24h no tiene ninguna observación con spread.

### 3.3 Spread del favorito y en ticks

La cuota mediana del favorito es **1,29** (P25 1,19 · P75 1,53).

| métrica | adaptive | hitos | TODAS |
|---|---|---|---|
| n | 792 | 64 | 856 |
| spread fav. P25 / P50 / P75 / P90 (%) | 9,3 / 13,2 / 15,7 / 18,2 | 9,3 / 13,4 / 15,9 / 18,1 | 9,3 / **13,2** / 15,8 / 18,2 |
| fav. ≤1 % / ≤2 % / ≤5 % / ≤10 % / >10 % | 0 / 0 / 12,5 / 27,5 / 72,5 | 0 / 0 / 10,9 / 26,6 / 73,4 | 0 / 0 / 12,4 / 27,5 / 72,5 |
| ticks del favorito, P50 | 16 | 17 | **16** |
| favorito a ≤1 tick / ≤3 ticks (%) | 0 / 0 | 0 / 0 | **0 / 0** |
| peor runner a ≤3 ticks / ≤10 ticks (%) | 0 / 10,6 | 0 / 10,9 | 0 / 10,6 |

### 3.4 `total_matched` (€)

| grupo | n | % > 0 | P25 | P50 | P75 | P90 | máx | P50 si > 0 |
|---|---|---|---|---|---|---|---|---|
| 24h | 3 | 0,0 | 0 | 0 | 0 | 0 | 0 | — |
| 12h | 29 | 0,0 | 0 | 0 | 0 | 0 | 0 | — |
| 6h | 37 | 2,7 | 0 | 0 | 0 | 0 | 20,00 | 20,00 |
| 1h | 53 | 11,3 | 0 | 0 | 0 | 1,59 | 361,20 | 43,05 |
| 10m | 58 | 12,1 | 0 | 0 | 0 | 23,67 | 1.326,00 | 136,00 |
| close | 58 | 12,1 | 0 | 0 | 0 | 23,67 | 1.406,00 | 136,00 |
| adaptive | 1.259 | 22,9 | 0 | 0 | 0 | 60,53 | 1.406,00 | 60,53 |
| A90-360 | 99 | 27,3 | 0 | 0 | 11,00 | 23,96 | 136,00 | 20,00 |
| A30-90 | 106 | 30,2 | 0 | 0 | 22,00 | 136,00 | 1.001,99 | 25,56 |
| A10-30 | 90 | 30,0 | 0 | 0 | 22,00 | 136,00 | 1.326,00 | 60,55 |
| A0-10 | 964 | 21,0 | 0 | 0 | 0 | 60,53 | 1.406,00 | 60,53 |
| todos los hitos | 238 | 8,8 | 0 | 0 | 0 | 0 | 1.406,00 | 60,54 |
| **TODAS** | 1.497 | 20,6 | 0 | 0 | 0 | 60,53 | 1.406,00 | 60,53 |

---

## 4. Métricas por MERCADO (cada mercado pesa 1)

Primero se agrega cada mercado dentro del grupo (mediana de liquidez y de spread, fracción de
observaciones con precio) y después se describe la distribución entre mercados. Esto elimina el
peso desproporcionado de los 26 mercados adaptive.

| grupo | mercados | % mercados con precio alguna vez | % liq ≥50 | fracción obs con precio (mediana, %) | liq. mediana P25 / P50 / P75 | mercados con spread | spread P25 / P50 / P75 / P90 | ≤1 % | ≤2 % | ≤5 % | ≤10 % | >10 % | `total_matched` máx P50 / P90 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 24h | 3 | 0,0 | 0,0 | 0,0 | — | 0 | — | — | — | — | — | — | 0 / 0 |
| 12h | 29 | 6,9 | 3,4 | 0,0 | 152 / 292 / 431 | 1 | 14,7 / 14,7 / 14,7 / 14,7 | 0 | 0 | 0 | 0 | 100 | 0 / 0 |
| 6h | 37 | 29,7 | 24,3 | 0,0 | 215 / 415 / 526 | 8 | 15,0 / 29,1 / 43,3 / 94,0 | 0 | 0 | 0 | 0 | 100 | 0 / 0 |
| 1h | 53 | 32,1 | 32,1 | 0,0 | 736 / 1.075 / 1.282 | 17 | 16,7 / 31,1 / 49,4 / 57,0 | 0 | 0 | 5,9 | 5,9 | 94,1 | 0 / 1,59 |
| 10m | 58 | 37,9 | 37,9 | 0,0 | 310 / 714 / 1.132 | 17 | 24,4 / 37,0 / 61,1 / 123,9 | 0 | 0 | 5,9 | 11,8 | 88,2 | 0 / 23,67 |
| close | 58 | 41,4 | 41,4 | 0,0 | 435 / 785 / 1.191 | 21 | 26,2 / 34,5 / 53,3 / 111,5 | 0 | 0 | 4,8 | 9,5 | 90,5 | 0 / 23,67 |
| adaptive | 26 | 100 | 100 | 92,5 | 449 / 770 / 1.138 | 24 | 24,4 / 33,6 / 53,3 / 80,4 | 0 | 0 | 4,2 | 8,3 | 91,7 | 0 / 306,60 |
| A90-360 | 11 | 100 | 100 | 100 | 357 / 576 / 1.017 | 10 | 15,8 / 21,9 / 42,7 / 57,6 | 0 | 0 | 0 | 0 | 100 | 0 / 25,56 |
| A30-90 | 19 | 94,7 | 94,7 | 100 | 470 / 1.014 / 1.243 | 17 | 22,3 / 28,0 / 49,4 / 57,0 | 0 | 0 | 5,9 | 11,8 | 88,2 | 0 / 181,04 |
| A10-30 | 20 | 90,0 | 90,0 | 100 | 642 / 1.051 / 1.268 | 17 | 19,9 / 27,7 / 49,4 / 63,2 | 0 | 0 | 5,9 | 11,8 | 88,2 | 0 / 158,52 |
| A0-10 | 26 | 96,2 | 96,2 | 92,1 | 455 / 757 / 1.278 | 23 | 26,8 / 34,5 / 53,8 / 82,6 | 0 | 0 | 4,3 | 4,3 | 95,7 | 0 / 306,60 |
| todos los hitos | 62 | 43,5 | 43,5 | 0,0 | 308 / 615 / 1.143 | 24 | 23,2 / 31,6 / 52,0 / 96,4 | 0 | 0 | 4,2 | 8,3 | 91,7 | 0 / 19,80 |
| **TODOS** | 62 | **43,5** | 43,5 | 0,0 | 413 / 685 / 1.105 | 25 | 24,8 / 32,8 / 52,9 / 78,2 | 0 | 0 | 4,0 | 8,0 | 92,0 | 0 / 27,00 |

**Spread del favorito por mercado** (mediana por mercado, n = 25 mercados con spread):
- Spread: P25 9,3 % · P50 **13,1 %** · P75 16,0 % · P90 18,0 %.
- Ticks del favorito: P50 16 · ticks del peor runner: P50 25.
- Mercados con spread del favorito ≤1 % / ≤2 % / ≤5 % / ≤10 % / >10 %: 0 / 0 / 8 / 28 / 72 %.
- Mejor spread del favorito visto en cualquier mercado: **2,56 %** (Ruud v Cerundolo, Laver Cup).
  Solo 5 mercados bajaron alguna vez del 3,5 %.

**Adaptive frente al resto:**

| | mercados | obs | con precio alguna vez | obs/mercado P50 | liq. máx. P50 | spread mediano P50 |
|---|---|---|---|---|---|---|
| no adaptive | 36 | 119 | 1 | 3 | 0 | 26,85 |
| adaptive | 26 | 1.378 | 26 | 54 | 1.126,62 | 33,64 |

De los 36 mercados no adaptive, solo uno (Mensik v Tien) mostró precio, y únicamente en el hito
`close`: por eso no llegó a activar la cadencia adaptive.

**Aparición del primer precio** (minutos antes del inicio, 27 mercados con precio): P25 12,9 ·
P50 67,8 · P75 379,7 · máx. 749,6.

---

## 5. Evolución dentro del mismo mercado (26 mercados adaptive)

### 5.1 Por banda (mediana por mercado; después, percentiles entre mercados)

| banda (min al inicio) | mercados | obs | fracción con precio (mediana) | liq P25 / P50 / P75 | spread P25 / P50 / P75 | ticks P50 | `total_matched` P50 / P75 |
|---|---|---|---|---|---|---|---|
| hito > 360 | 20 | 40 | 0,33 | 0 / 0 / 222 | 15,0 / 27,1 / 37,1 | 21 | 0 / 0 |
| 90–360 | 12 | 100 | 1,00 | 268 / 509 / 976 | 15,8 / 21,9 / 42,7 | 18,25 | 0 / 5 |
| 30–90 | 26 | 132 | 1,00 | 0 / 559 / 1.133 | 22,3 / 30,0 / 49,4 | 28,5 | 0 / 0 |
| 10–30 | 26 | 115 | 1,00 | 280 / 669 / 1.132 | 24,9 / 32,8 / 54,3 | 26 | 0 / 16,5 |
| 3–10 | 25 | 649 | 0,96 | 267 / 568 / 1.091 | 24,8 / 32,8 / 53,3 | 25 | 0 / 22 |
| 0–3 | 26 | 342 | 0,89 | 205 / 598 / 1.135 | 28,2 / 34,5 / 53,8 | 24 | 0 / 16,5 |

### 5.2 Primera frente a última observación con precio

- **Spread:** 18 mercados tienen spread en ambos extremos. Mejora en 10, empeora en 4 y no
  cambia en 4.
- **Liquidez:** crece en 14 de 26 mercados. Ratio mediano entre última y primera: **×1,48**.
- **`total_matched`:** crece en 7 de 26 mercados. La mediana en la última observación es 0.
- **Precio del favorito:** se mueve un **1,3 %** en mediana (|log(última/primera)|).

La tabla mercado a mercado está en `audit_out.txt` (sección T9).

### 5.3 Hitos del mismo mercado comparados entre sí

| transición | mercados con ambos hitos | con precio en el 1.º / en el 2.º | con spread en ambos | spread P50 en el 1.º → en el 2.º | mercados que mejoran |
|---|---|---|---|---|---|
| 6h → 1h | 35 | 11 / 16 | 7 | 31,25 → 31,05 | 6 |
| 1h → 10m | 52 | 17 / 22 | 13 | 31,05 → 32,81 | 4 |
| 10m → close | 57 | 22 / 24 | 17 | 36,99 → 38,71 | 3 |
| 1h → close | 52 | 17 / 24 | 15 | 31,05 → 32,81 | 2 |

**Conclusión: el spread no se estrecha al acercarse el inicio.**

---

## 6. Operación del collector (últimos 7 días)

Ventana: 2026-09-20 08:49 → 2026-09-27 08:49 CEST. Fuente: journal de `edgecourt-collector`.

| Métrica | Valor |
|---|---|
| Ciclos completados | **10.054** |
| Ciclos fallidos | **0** |
| Ciclos esperados al ritmo real (~60,16 s por ciclo) | ~10.054: ningún hueco |
| Intervalo entre ciclos P50 / P90 / P99 / máx. | **60,13 s** / 60,20 s / **60,57 s** / 63,8 s |
| Huecos > 90 s | 0 |
| Errores o avisos en la ventana | 0 |
| Servicio | `active (running)` desde el 2026-09-18 16:00:54 CEST, sin reinicios |

### 6.1 Autenticación: `INVALID_SESSION_INFORMATION` cada 20 minutos

| Métrica | Valor |
|---|---|
| Eventos en la ventana | 503 (~72 al día; 52 el 20-sep y 20 el 27-sep, días parciales) |
| Eventos desde el arranque del servicio | 624 |
| Intervalo entre eventos | media **20,05 min**, desviación típica 0,007 min (20,04–20,12) |
| Ciclo que sigue a cada evento | **503 de 503 completados** |
| Duración de la reautenticación | ~0,3 s |

**Mecanismo verificado en el código:**
- `ReadOnlyBettingClient._call` detecta el código de sesión inválida, llama a
  `SessionManager.invalidate()` y reintenta dentro del mismo bucle de intentos, que hace un login
  nuevo.
- `KEEP_ALIVE_INTERVAL = timedelta(hours=1)` (`market/auth.py`): el `keepAlive` nunca llega antes
  de que la sesión caduque, a los ~20 min.

**Impacto actual:** benigno. Supone 1 llamada fallida y 1 login cada 20 min, sin pérdida de
datos. **No se modificó.** Pasa a [Phase 3.5-A](../phases/PHASE_03_5_MARKET_VALIDATION.md).

### 6.2 Errores de red y de arranque (todos fuera de la ventana)

- **2 fallos de DNS** (`Temporary failure in name resolution` durante el login), el 18-sep a las
  15:54 y a las 16:00 CEST. Ocurrieron justo después de dos arranques de la máquina y se
  recuperaron en el ciclo siguiente, ~2 min después, gracias al backoff.
- Antes de instalar el servicio definitivo, el 18-sep, `collector.log` registra:
  - 2 `FileNotFoundError` (configuración de certificados incompleta);
  - 2 «faltan credenciales»;
  - 3 errores con el mensaje `__filename: string`.

  Son del periodo de puesta en marcha y no se han vuelto a producir.
- El aviso de systemd `Unknown key 'StartLimitIntervalSec' in section [Service]` es del 18-sep a
  las 15:43. El commit `c292cbd` lo corrigió y no ha vuelto a aparecer.

### 6.3 Huecos en los datos que no son caídas

| día (CEST) | observaciones | mercados | adaptive | con precio | mercados que empiezan ese día |
|---|---|---|---|---|---|
| 2026-09-18 | 152 | 8 | 125 | 130 | 5 |
| 2026-09-19 | 603 | 19 | 521 | 346 | 18 |
| 2026-09-20 | 269 | 9 | 231 | 225 | 10 |
| 2026-09-21 | **0** | 0 | 0 | 0 | 0 |
| 2026-09-22 | 8 | 3 | 0 | 0 | 3 |
| 2026-09-23 | 8 | 3 | 0 | 0 | 3 |
| 2026-09-24 | 136 | 9 | 117 | 74 | 6 |
| 2026-09-25 | 210 | 9 | 181 | 159 | 10 |
| 2026-09-26 | 110 | 7 | 84 | 62 | 7 |
| 2026-09-27 | 1 | 1 | 0 | 0 | 3 |

El 21-sep el collector funcionaba con normalidad: todos sus ciclos se completaron. Simplemente
el catálogo estaba vacío. En la ventana, la mediana de mercados visibles por ciclo es **0** y el
máximo es 9. El límite de `maxResults = 200` de `listMarketCatalogue` no llegó a tocarse.

### 6.4 Cadencia adaptive real

Intervalo desde la observación anterior del mismo mercado, contando también los hitos:

| cadencia esperada | n | P50 (min) | P90 (min) | máx. (min) | % intervalos > 1,5 × esperado |
|---|---|---|---|---|---|
| 1 min | 964 | 1,00 | 2,01 | 12,05 | 11,2 |
| 5 min | 90 | 5,01 | 8,03 | 12,04 | 21,1 |
| 10 min | 106 | 10,04 | 16,23 | 30,10 | 10,4 |
| 30 min | 99 | 30,09 | 30,10 | 30,12 | 0,0 |

Los desfases de 3–12 min en el tramo de 1 min coinciden con cambios de banda y con capturas de
hitos. No se ha profundizado más.

---

## 7. Anomalías de calidad de datos

1. **`market_start_time` se desplaza después de capturar los hitos** (crítico para el CLV).
   - **38 de 62 mercados** retrasaron su hora de inicio publicada; en el caso extremo, 900 min.
     Afecta a 1.175 de las 1.259 observaciones adaptive.
   - Hitos cuya hora de inicio implícita (`observed_at + minutes_to_start`) difiere en más de
     5 min del último inicio publicado:

     | hito | 24h | 12h | 6h | 1h | 10m | close |
     |---|---|---|---|---|---|---|
     | desplazados | 2/3 | 26/29 | 32/37 | 44/53 | 48/58 | **45/58** |

   - En esos 45 `close`, la distancia real al último inicio publicado era, en su mayoría, de
     11–150 min (un caso llegó a 963 min).
   - **Por tanto, `close` NO es actualmente un precio de cierre fiable.** Sirve como «captura a
     ~2 min del inicio *previsto en ese momento*».
   - Además, cada nuevo retraso vuelve a meter al mercado en la ventana de 0–10 min. Eso explica
     las 964 observaciones en A0-10: unas 37 por mercado, frente a las ~10 que prevé
     `estimate_daily_observations`.
   - La hora de inicio publicada no es necesariamente la hora real de inicio. No se ha medido
     cuándo pasó cada mercado a `inplay`.
2. **Libros con precio en un solo lado:** 140 observaciones tienen precio pero no spread (152 si
   se cuenta por runner). Hay 25 observaciones con precio en las que uno de los runners no tiene
   ninguno.
3. **Mercados observados en juego o no abiertos:** 5 con `inplay = true`, 2 `CLOSED` y 1
   `SUSPENDED`. Se incluyeron en las métricas; su impacto es despreciable.
4. **Integridad:** no hay spreads negativos ni iguales a cero, ni `(market_id, capture_key)`
   duplicados.

---

## 8. Problemas registrados

| ID | Problema | Severidad | Estado | Dónde se trata |
|---|---|---|---|---|
| A | Sesión que caduca cada ~20 min; `KEEP_ALIVE_INTERVAL` a 1 h. Se recupera sola | Baja (operativa) | **No corregido** a propósito | Phase 3.5-A |
| B | `close` no refleja el cierre real cuando se retrasa `market_start_time` | **Crítica para el CLV** | **No corregido** a propósito | Phase 3.5-B |
| C | Calidad de mercado muy baja: spread del favorito ~13,2 %, ~16 ticks, `top_depth` ~67 €, solo el 43,5 % de mercados con precio | Alta | Observación, **no regla** | Phase 3.5-C / D |
| D | La muestra no contiene torneos ATP/WTA regulares | Alta | Causa desconocida | Phase 3.5-C |

**Sobre C:** estas cifras **no deben convertirse todavía en reglas permanentes** (filtros,
umbrales ni un cambio de `MINIMUM_LIQUIDITY`). La muestra es pequeña (62 mercados, 9 días) y está
dominada por competiciones especiales.

---

## 9. Conclusiones descriptivas

1. Con la calidad observada, cualquier edge del orden del umbral por defecto (`MINIMUM_EDGE` =
   0,03) queda por debajo del coste de cruzar el spread. Un spread del favorito del 13 % equivale
   a pagar ~6,5 % por lado respecto al precio medio.
2. Con estos datos, `MINIMUM_LIQUIDITY` = 50 no discrimina ningún mercado útil. Aun así, **no se
   cambia**.
3. Las métricas por observación sobrerrepresentan los mercados más activos. Las conclusiones se
   apoyan en las métricas por mercado.
4. No se puede afirmar todavía si la escasez de mercados y su baja calidad son estructurales (la
   jurisdicción, el endpoint o la clave de la API) o coyunturales (el calendario). Eso lo decide
   Phase 3.5-C.
5. El collector es operativamente sólido, pero **su definición de `close` debe rediseñarse antes
   de medir CLV**.

---

## 10. Reproducibilidad

Los scripts usados están en
[`2026-09-week1-market-audit/SCRIPTS.md`](2026-09-week1-market-audit/SCRIPTS.md), copiados
literalmente como Markdown para que no entren en el lint del proyecto:

| Script | Qué hace |
|---|---|
| `q.py` | Ejecuta SQL de solo lectura contra la base operativa usando la configuración del proyecto. El DSN nunca se imprime |
| `audit.py` | Tablas T1–T14: cobertura, liquidez, spread, `total_matched`, métricas por mercado, evolución, anomalías y cadencia |
| `ticks.py` | Spread del favorito y spread en ticks |
| `check.py` | Desplazamiento de `market_start_time`, huecos de cadencia y mercados duplicados |
| `logs.py` | Ciclos, huecos, errores y reautenticaciones a partir del journal y de `collector.log` |

La salida completa de `audit.py` está en
[`audit_out.txt`](2026-09-week1-market-audit/audit_out.txt). Incluye el listado de los 62
mercados (T7) y la comparación mercado a mercado (T9).

Los scripts reciben como argumento un directorio de trabajo, donde escriben los CSV intermedios
(`observations.csv`, `runner_price.csv` y `per_market.csv`). Esos CSV no se versionan.
**Volver a ejecutarlos hoy daría cifras distintas**, porque el collector sigue escribiendo. Las
cifras de este documento son las del corte indicado arriba.

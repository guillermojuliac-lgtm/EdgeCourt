# Investigación 3.5-C2 — Catálogo ATP del Exchange español (PROTOCOLO)

> **ESTADO: CLOSED** — ventana del **2026-09-29 00:00 UTC** al **2026-10-19 00:00 UTC**, completada.
> - Herramienta **IMPLEMENTED** y timer **INSTALLED** (2026-09-27).
> - **Resultado: A** (con matices). Ver [Resultados](#resultados), revisión del 2026-11-03.
>
> Este documento es el **protocolo, fijado antes de ver los datos**. Las conclusiones están en la
> sección *Resultados*, añadida al cerrar el experimento; el protocolo no se ha modificado.

| Campo | Valor |
|---|---|
| Fase | [Phase 3.5-C2](../phases/PHASE_03_5_MARKET_VALIDATION.md) |
| Antecedente | [Investigación 3.5-C](2026-09-atp-wta-catalogue.md) (catálogo `.es` restringido, evidencia empírica) |
| Protocolo fijado | 2026-09-27 |
| Ventana | 2026-09-29 00:00 UTC → 2026-10-19 00:00 UTC |
| Herramienta | `edgecourt betfair catalogue-audit` / `catalogue-report` (`src/edgecourt/market/catalogue_audit.py`, `catalogue_report.py`) |

---

## 1. Pregunta e hipótesis

**Pregunta:** ¿qué catálogo de tenis devuelve la sesión española durante torneos ATP regulares
de mayor categoría (ATP 500 de Pekín y Tokio, Masters 1000 de Shanghái)?

| Hipótesis | Enunciado |
|---|---|
| **H1** | El catálogo `.es` **sí** ofrece partidos `MATCH_ODDS` de ATP 500/1000. La ausencia de la muestra 3.5-C se debió al tipo de torneos de esas fechas |
| **H0** | El catálogo `.es` **no** ofrece partidos ATP 500/1000 aunque se estén jugando, igual que no ofreció ATP 250, WTA ni Challengers en 3.5-C |

**Preguntas operativas** (por torneo):
1. ¿Aparece el torneo en el catálogo de la sesión `.es`?
2. ¿Aparecen partidos `MATCH_ODDS`?
3. ¿Cuántos?
4. ¿Con cuánta antelación aparecen respecto a `marketStartTime`?
5. ¿Tienen BACK y LAY?
6. ¿Qué spread presentan (peor runner, favorito y ticks)?
7. ¿Qué liquidez presentan (`total_available`, `top_depth`)?
8. ¿Hay `totalMatched` a nivel de mercado? (Por selección no está disponible con la Delayed Key.)
9. ¿Los descubre también el collector normal (`betfair_market`)?
10. ¿Cambian estas respuestas al acercarse el partido?

## 2. Torneos objetivo y fuentes oficiales de calendario

| Torneo | Categoría | Fechas | Fuente |
|---|---|---|---|
| China Open (Pekín) | ATP 500 | qualy 28–29 sep; cuadro principal 30 sep – 6 oct | [atptour.com, China Open 2026](https://www.atptour.com/en/news/beijing-atp-500-2026-history-draw-schedule); calendario ATP 2026 (PDF, 18-ago-2026) |
| Kinoshita Group Japan Open (Tokio) | ATP 500 | semana del 30 sep | Calendario ATP 2026 (PDF): `https://www.atptour.com/-/media/files/calendar-pdfs/2026/2026-27-atp-challenger-calendar-as-of-18-aug-2026.pdf` |
| Rolex Shanghai Masters | ATP Masters 1000 | desde el 7 oct (dos semanas) | Calendario ATP 2026 (PDF) |
| China Open (Pekín) | WTA 1000 | semana del 28 sep (dos semanas) | Calendario WTA 2026 (PDF): `https://wtafiles.wtatennis.com/pdf/calendar/calendar.pdf` |
| Challengers ATP y WTA 125 de esas semanas | varios | ver PDF | ídem |

Las fechas exactas por día se volverán a confirmar con las fuentes oficiales al cerrar el
experimento, y cualquier discrepancia se registrará. «Había torneo en el calendario» **no**
implica que Betfair debiera ofrecer el mercado.

## 3. Metodología

**Captura** (`catalogue-audit`). Un proceso corto cada 30 minutos, **solo lectura**, separado
del collector:
1. Login en la sesión `.es` con la configuración existente.
2. `listMarketCatalogue` **amplio**: `{"eventTypeIds": ["2"]}`, sin tipo de mercado, competición
   ni ventana temporal, `maxResults = 1000`.
3. `listMarketCatalogue` de `MATCH_ODDS`: `{"eventTypeIds": ["2"], "marketTypeCodes":
   ["MATCH_ODDS"]}`, sin ventana.
4. `listMarketBook` de esos `MATCH_ODDS`, con `EX_BEST_OFFERS`, profundidad 3 y `virtualise`,
   igual que el collector.
5. Escritura atómica de los artefactos y logout.

La captura **no se conecta a PostgreSQL**.

**Informe** (`catalogue-report`):
- Agrega los artefactos, calcula las métricas y hace el cruce con `betfair_market` en una sesión
  PostgreSQL de solo lectura (`connection.read_only = True`).
- Las definiciones de las métricas son las del collector y las de la
  [auditoría Semana 1](../audits/2026-09-week1-market-audit.md) §2.

**Frecuencia:** cada 30 minutos (`OnCalendar=*:00,30 UTC`, `RandomizedDelaySec=120`): 48 slots
al día y **960 slots** en la ventana. La evolución fina cerca del partido (preguntas 5–7 y 10)
la aporta además el collector normal, cada minuto en la parte final, si descubre esos mercados.

## 4. Almacenamiento e idempotencia

```
data/research/catalogue_audit/          (fuera de git; permisos 700/600)
  catalogues/<sha256>.json.gz           catálogo normalizado, direccionado por contenido
  YYYY-MM-DD/HHMM_books.json.gz         libros del slot: {run_id, market_ids, books}
  YYYY-MM-DD/HHMM_run.json              registro del slot (se escribe el último)
  logs/                                 logs de la aplicación bajo systemd
```

- **Un slot = un artefacto.** Cada slot deja su `run.json` aunque el catálogo no haya cambiado:
  el `run.json` apunta al `catalogue_hash`.
- **Se conserva el primer snapshot válido del slot.** Si ya existe un `run.json` con
  `status = ok`, una nueva ejecución del mismo slot termina sin llamar a Betfair. Un `run.json` con
  `status = error` no es una observación válida: un reintento en el mismo slot lo sustituye
  atómicamente y guarda el intento fallido en `previous_attempts`. Un `flock` serializa las
  ejecuciones simultáneas.
- **Escritura atómica:** temporal + `fsync` + `rename`. El `run.json` se escribe el último, así
  que un proceso interrumpido nunca deja un run válido a medias.
- **Libros reconstruibles:** `books.json.gz` lleva el `run_id`, y el `run.json` lleva el
  `books_sha256`. El informe verifica ambos.
- **Nunca** se guardan tokens, App Key, contraseñas, certificados, cabeceras ni DSN. Los errores
  se sanitizan.

## 5. Métricas

**Por ejecución:**
- estado;
- número de mercados de tenis, `MATCH_ODDS`, competiciones y libros;
- `market_data_delayed`;
- duración.

**Por mercado:**
- competición y evento;
- primera y última vez visto (slot);
- **antelación** = `marketStartTime` − primera captura (horas);
- número de slots presente;
- `in_collector` (cruce con `betfair_market`).

**Por libro y slot:**
- estado, `inplay`, minutos al inicio;
- BACK y LAY presentes;
- `total_available`, `top_depth`;
- spread del peor runner (%), spread del favorito (%) y spread en ticks;
- `totalMatched` del mercado.

**Cobertura** = runs `ok` / slots esperados, global y por ventana de torneo.

## 6. Criterios de conclusión (fijados de antemano)

**Criterio de validez: cobertura ≥ 95 %** de runs `ok` sobre los slots esperados, en cada una
de estas ventanas:

| Ventana | Desde (UTC) | Hasta (UTC) | Slots |
|---|---|---|---|
| Pekín/Tokio, cuadro principal | 2026-09-30 00:00 | 2026-10-07 00:00 | 336 |
| Shanghái | 2026-10-07 00:00 | 2026-10-19 00:00 | 576 |
| Global | 2026-09-29 00:00 | 2026-10-19 00:00 | 960 |

Además, se documentará con fuente oficial que los torneos se jugaron en esas fechas.

| Resultado | Condición |
|---|---|
| **A — `.es` sí ofrece ATP 500/1000** | Validez cumplida y **al menos un** mercado `MATCH_ODDS` de Pekín, Tokio o Shanghái en el catálogo `.es`. Se responden las preguntas 1–10 por torneo |
| **B — `.es` no ofreció ATP 500/1000 en la ventana** | Validez cumplida y **cero** mercados de esos torneos. Se refuerza con ≥ 2 comprobaciones manuales de la web pública de betfair.com en días de partido, fuera del proceso automático. Aun así, la ausencia de confirmación oficial de Betfair se sigue registrando como **NO VERIFICADO** |
| **C — evidencia insuficiente** | Validez no cumplida, o datos corruptos o incompletos que impidan A o B |

La **clasificación de mercados por torneo** (qué competición de Betfair corresponde a
Pekín, Tokio o Shanghái) se hará por el nombre y el id de competición del catálogo, y quedará
documentada mercado a mercado en el informe final.

## 7. Limitaciones conocidas de antemano

- **Delayed Key:** los precios llegan con 1–180 s de retraso y sin volumen casado por selección.
  Sirve para presencia, antelación y calidad aproximada del libro; no para medir el cierre fino.
- **Resolución de 30 min** para la aparición de mercados: la antelación tiene un error de hasta
  30 min más el retraso aleatorio.
- **Un único punto de observación** (la cuenta `.es`). Sin otra cuenta, la comparación con .com
  solo es posible por comprobaciones manuales de la web pública.
- Los slots perdidos (máquina apagada, red) **no se recuperan** (`Persistent=false`): cuentan
  contra la cobertura.
- El experimento puede coincidir con cambios de catálogo de Betfair que no podremos atribuir.

## 8. Cómo reproducir el informe

```bash
# Informe completo de la ventana oficial (cruce con betfair_market en solo lectura)
uv run edgecourt betfair catalogue-report \
    --out docs/investigations/2026-10-spanish-exchange-atp-catalogue

# Sin PostgreSQL
uv run edgecourt betfair catalogue-report --no-db --out <dir>

# Ventana concreta (p. ej. Shanghái)
uv run edgecourt betfair catalogue-report --since 2026-10-07T00:00Z --until 2026-10-19T00:00Z --out <dir>
```

El informe genera `runs.csv`, `markets.csv`, `books.csv` y `summary.json`. Al cerrar, se
versionan en `docs/investigations/2026-10-spanish-exchange-atp-catalogue/`, junto con
`SHA256SUMS` del conjunto bruto de `data/research/catalogue_audit/`. El conjunto bruto se
archiva comprimido con su hash, para poder regenerar el informe.

## 9. Operación

- **Instalación** (manual, requiere `sudo`): `bash scripts/install_catalogue_audit.sh`. Muestra
  las unidades y pide confirmación.
- **Seguimiento:** `journalctl -u edgecourt-catalogue-audit` y `ls data/research/catalogue_audit/`.
- **Fin:** `sudo systemctl disable --now edgecourt-catalogue-audit.timer`. Si se olvida, el
  comando no llama a Betfair a partir del 2026-10-19 00:00 UTC.

## 10. Prueba previa (no forma parte del experimento)

El 2026-09-27 a las 09:55 UTC se hizo una ejecución real de prueba en un directorio temporal
(`--since 2026-09-27`), fuera del experimento. Resultado:
- login `.es` correcto;
- 7 mercados de tenis (Laver Cup 4, BJK Cup 3), todos `MATCH_ODDS`;
- `market_data_delayed = true`;
- artefactos con permisos 600/700 y sin secretos;
- una segunda ejecución del mismo slot devolvió `already_captured` sin llamar a Betfair;
- `catalogue-report` los leyó y cruzó con `betfair_market` en solo lectura (los 7 también en el
  collector).

Esos datos **no** se usan en el experimento.

---

## Resultados

> Revisión final del **2026-11-03**, sobre la ventana completa. Informe generado con
> `catalogue-report --since 2026-09-29T00:00Z --until 2026-10-19T00:00Z` (cruce con
> `betfair_market` en sesión de solo lectura). Ficheros en
> [`2026-10-spanish-exchange-atp-catalogue/`](2026-10-spanish-exchange-atp-catalogue/):
> `summary.json`, `runs.csv`, `markets.csv`, `books.csv` y `SHA256SUMS` del conjunto bruto
> (`data/research/catalogue_audit/`, sin `logs/`, 2.757 ficheros).

### R1. Validez (criterio §6)

| Ventana | Slots esperados | Runs `ok` | Cobertura |
|---|---|---|---|
| Pekín/Tokio (30-sep → 7-oct) | 336 | 336 | **100 %** |
| Shanghái (7-oct → 19-oct) | 576 | 576 | **100 %** |
| Global | 960 | 960 | **100 %** |

0 runs con error. El 100 % de los runs y de las filas de libro llevan `market_data_delayed = true`.
**Validez cumplida** en las tres ventanas (umbral: 95 %).

**Fuentes oficiales de calendario (requisito de validez del §6), consulta del 2026-11-03:**

| Torneo | Estado | Detalle |
|---|---|---|
| ATP Beijing (China Open, ATP 500) | **CONFIRMADO que se jugó** | atptour.com (página del torneo, cuadro y resultados): clasificación 28–29-sep; cuadro principal 30-sep → 6-oct; final individual el 6-oct (Djokovic v De Minaur, resultado publicado). Coincide con el mercado *Djokovic v De Minaur* de C2 (6-oct 11:00 UTC). La página de cuadro muestra 16 partidos de primera ronda, es decir, un cuadro de 32 (inferencia, no dato publicado) |
| ATP Tokyo (Japan Open) | **NO VERIFICADO** | La URL de referencia devolvía otro torneo; no se localizó la página correcta |
| ATP Shanghai (Masters 1000) | **NO VERIFICADO** | Ídem |
| WTA Beijing y WTA Wuhan | **NO VERIFICADO** | Los PDF de calendario no contienen texto legible y la web WTA carga el listado por JavaScript |

Las comprobaciones manuales de las webs públicas **no se han repetido** desde la revisión
intermedia del 5-oct. Con Tokio y Shanghái sin verificar, la validez por calendario queda
**incompleta** para esos dos torneos; no afecta a la cobertura (100 %), que sí está medida.

### R2. Resultado frente a las hipótesis

**Resultado A** según el criterio fijado: validez cumplida y al menos un `MATCH_ODDS` de Pekín o
Shanghái en el catálogo `.es`. **H0 queda refutada para Shanghái**; para Pekín y Tokio el
resultado es parcial. El criterio A no distingue cuánto se ofrece, y la diferencia importa:

| Competición (id Betfair) | Mercados | Individuales | Dobles | Primera vez visto | Último inicio |
|---|---|---|---|---|---|
| WTA Beijing 2026 (12833957) | 132 (130 `MATCH_ODDS` + 2 de otro tipo) | 103 | 29 | 29-sep | 11-oct |
| WTA Wuhan 2026 | 110 | 84 | 26 | 9-oct | 18-oct |
| **ATP Beijing 2026 (12834053)** | **4** | **3** | 1 | 30-sep 23:00 | 6-oct |
| **ATP Shanghai 2026 (12835824)** | **161** | **132** | 29 | 4-oct 11:00 | 18-oct |
| ATP Tokyo | **0** | — | — | — | — |
| Challenger (ATP y WTA 125) | **0** | — | — | — | — |

Total: 407 mercados (405 `MATCH_ODDS`) en 4 competiciones.

- **ATP Pekín:** solo 3 individuales (*Yu Bu v Djokovic*, *De Minaur v Hurkacz*, *Djokovic v De
  Minaur*) y 1 de dobles. Todos los individuales incluyen a Djokovic o De Minaur; el cuadro individual
  era de 32 jugadores (31 partidos, inferido de la página oficial de cuadro), de modo que `.es`
  ofreció 3 de 31. Es una observación; **el criterio con el que Betfair publica unos partidos y no otros no está
  verificado**.
- **ATP Shanghái:** 132 individuales: 36 con inicio el 5 y 6-oct (24 + 12) y 96 desde el 7-oct
  (14, 19, 16, 16, 8, 8, 4, 4, 2, 2, 2 y 1 por día). Que sean anteriores al cuadro principal
  los de antes del 7-oct (probablemente clasificación) y que el cuadro principal esté completo
  **no está contrastado con el cuadro oficial**.
- **ATP Tokyo y Challenger: 0** mercados en toda la ventana. La causa (calendario, jurisdicción
  `.es`, criterio de Betfair) **no está verificada**.

### R3. Antelación (pregunta 4), solo individuales

| Competición | P50 (h) | Mín – máx (h) |
|---|---|---|
| ATP Shanghai | 37,5 | 5,5 – 76,8 |
| ATP Beijing | 19,8 | 14,0 – 37,5 |
| WTA Beijing | 40,1 | 0,4 – 60,1 |
| WTA Wuhan | 19,8 | 0,4 – 65,7 |

Resolución: ±30 min más el retraso aleatorio del timer (§7).

### R4. Calidad del libro (preguntas 5–8 y 10), individuales, antes del inicio

| Competición | Obs. | Con precios | BACK y LAY | Spread favorito P50 | Ticks P50 | `top_depth` P50 | `totalMatched` > 0 |
|---|---|---|---|---|---|---|---|
| **Todo el periodo previo** | | | | | | | |
| ATP Shanghai | 9.152 | 82,1 % | 81,3 % | 5,4 % | 11 | 23 | 39,3 % |
| ATP Beijing (3 mercados) | 143 | 100 % | 100 % | 3,9 % | 14 | 18 | 58,7 % |
| WTA Beijing | 7.289 | 90,9 % | 90,4 % | 5,1 % | 12 | 17 | 37,4 % |
| WTA Wuhan | 3.868 | 86,1 % | 85,4 % | 6,3 % | 12 | 25 | 26,8 % |
| **Últimos 60 min** | | | | | | | |
| ATP Shanghai | 420 | 98,3 % | 96,9 % | 4,0 % | 9 | 21 | 73,1 % |
| ATP Beijing | 10 | 100 % | 100 % | 3,6 % | 7,5 | 20 | 100 % |
| WTA Beijing | 263 | 99,6 % | 99,6 % | 3,0 % | 8 | 17 | 87,8 % |
| WTA Wuhan | 243 | 95,9 % | 95,9 % | 4,2 % | 9 | 25 | 59,3 % |

- La calidad **mejora al acercarse el partido** (pregunta 10): en Shanghái, BACK y LAY pasan del
  81 % al 97 % en la última hora.
- Cifras **descriptivas con Delayed Key** (retraso 1–180 s, sin volumen por selección): sirven
  para presencia y calidad aproximada, no para medir el cierre fino. ATP Pekín es una muestra
  de 3 mercados y 143 observaciones.
- Mercados con precio alguna vez: ATP Shanghái 92,5 %, WTA Beijing 93,1 %, WTA Wuhan 89,1 %,
  ATP Beijing 75 % (3 de 4).
- Comparación con la Semana 1 (otra muestra, sin tratamiento estadístico): spread del favorito
  P50 de 13,2 % entonces frente a 3,6–6,3 % ahora en torneos regulares.

### R5. Cruce con el collector (pregunta 9)

De los 407 mercados, **85 no están en `betfair_market`**: 3 de ATP Pekín, 24 de ATP Shanghái y
58 de WTA Pekín. **Los 85 tienen inicio anterior al 5-oct 13:17 UTC**, es decir, aparecieron
durante la caída del collector (incidente de particiones). **Ningún mercado posterior a la
recuperación falta**: no hay problema de descubrimiento del collector.

### R6. Limitaciones y NO VERIFICADO

- Calendarios oficiales: solo ATP Pekín confirmado; Tokio, Shanghái y WTA, **NO VERIFICADO** (ver R1). Comprobaciones manuales de betfair.com: **no repetidas**.
- Por qué `.es` ofrece 3 individuales de Pekín y no Tokio ni Challenger: **no verificado**; sin
  confirmación oficial de Betfair.
- Un único punto de observación (cuenta `.es`) y Delayed Key, como se anticipó en el §7.
- Los 24 mercados de Shanghái del 5-oct anteriores al cuadro principal: naturaleza no contrastada.

### R7. Qué implica y qué no

- **Implica:** la hipótesis «`.es` no ofrece ATP de nivel alto» no se sostiene: Shanghái (Masters
  1000) se ofreció en volumen. El objetivo ATP prematch ([DEC-002](../DECISIONS.md#dec-002))
  **no queda descartado por disponibilidad**, pero el volumen es muy desigual entre torneos
  (Shanghái 132 individuales, Pekín 3, Tokio 0).
- **No implica:** que el volumen y la calidad sean suficientes para validar valor o CLV. Eso lo
  decide la fase de Value con más muestra, y la decisión sobre la fuente de mercado y el alcance
  es del responsable del proyecto. **DEC-002 no se modifica** en esta revisión.

### R8. Cierre operativo

El timer `edgecourt-catalogue-audit.timer` sigue activo tras el 19-oct; el comando no llama a
Betfair fuera de la ventana (§9), por lo que no genera datos nuevos. Desactivarlo requiere
`sudo systemctl disable --now edgecourt-catalogue-audit.timer` (§9) y lo ejecuta el responsable.

**Actualización (2026-11-03):** el responsable lo desactivó ese mismo día; verificado `disabled` e
`inactive`, sin ninguna entrada en `systemctl list-timers`. La unidad `.service` sigue instalada.

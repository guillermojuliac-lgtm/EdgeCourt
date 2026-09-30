# Investigación 3.5-C2 — Catálogo ATP del Exchange español (PROTOCOLO)

> **ESTADO: RUNNING** desde el **2026-09-29 a las 00:00 UTC**; termina el **2026-10-19 a las
> 00:00 UTC**.
> - Herramienta **IMPLEMENTED** y timer **INSTALLED** (2026-09-27).
> - El seguimiento intermedio, que **no** son conclusiones, está en el
>   [documento de la fase](../phases/PHASE_03_5_MARKET_VALIDATION.md) (3.5-C2).
>
> Este documento es el **protocolo, fijado antes de ver los datos**. Las conclusiones se
> añadirán en una sección nueva al cerrar el experimento, sin modificar el protocolo.

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

*(Vacío hasta el cierre del experimento. No se escriben conclusiones antes de tener los datos.)*

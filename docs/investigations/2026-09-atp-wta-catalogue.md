# Investigación 3.5-C — ¿Por qué no aparecieron torneos ATP/WTA regulares?

> **Documento inmutable una vez cerrado.** Refleja la evidencia disponible el **2026-09-27**.
> Convención en [`docs/README.md`](../README.md).

| Campo | Valor |
|---|---|
| Fecha | 2026-09-27 |
| Fase | [Phase 3.5-C](../phases/PHASE_03_5_MARKET_VALIDATION.md) |
| Punto de partida | [Auditoría Semana 1](../audits/2026-09-week1-market-audit.md) |
| Modo | **Solo lectura.** No se modificó ni el collector, ni PostgreSQL, ni el esquema, ni la configuración |
| Evidencias y scripts | [`2026-09-atp-wta-catalogue/`](2026-09-atp-wta-catalogue/) |

**Clasificación de afirmaciones:**
- **HECHO:** observado directamente y reproducible con los artefactos guardados.
- **EVIDENCIA:** fuente externa citada, con su fiabilidad indicada.
- **HIPÓTESIS:** explicación plausible sin demostrar.
- **NO VERIFICADO:** no se pudo comprobar.

---

## 1. Pregunta de investigación

Durante la primera muestra (18–27 sep 2026) EdgeCourt observó 62 mercados de solo tres
competiciones: Davis Cup, Billie Jean King Cup y Laver Cup. **¿Por qué no apareció ningún
torneo ATP ni WTA regular?**

Hipótesis a distinguir:

| Letra | Hipótesis |
|---|---|
| A | Calendario real |
| B | Filtros de EdgeCourt |
| C | Filtrado por `marketType`, `eventType` o competición |
| D | Comportamiento de `listEvents` / `listMarketCatalogue` |
| E | Rango temporal solicitado |
| F | Paginación o `maxResults` |
| G | Jurisdicción o cuenta `.es` |
| H | Diferencias entre la web y la API |
| I | Otros problemas de descubrimiento |

## 2. Resumen de la conclusión

**Clasificación: F) combinación de A) calendario, en parte, y D) efecto de la jurisdicción,
con evidencia empírica fuerte.** No hay ningún bug ni filtro de EdgeCourt (B descartada).

1. **EdgeCourt no pierde mercados** (HECHO):
   - la consulta del collector devuelve exactamente lo mismo que el catálogo de tenis completo,
     sin filtros, que Betfair sirve a la sesión;
   - `betfair_market` guarda todo lo que devuelve la API, y en 9 días nunca devolvió un mercado
     ATP ni WTA regular.
2. **El catálogo que Betfair sirve a esta cuenta está muy restringido** (HECHO, 2026-09-27
   07:22 UTC):
   - solo hay 2 disciplinas en todo el exchange: Soccer (2.995 mercados) y Tennis (3);
   - en tenis solo aparece la Laver Cup (3 mercados `MATCH_ODDS`);
   - en ese mismo momento se estaban jugando ATP Chengdu, ATP Hangzhou, WTA Singapur, WTA Seúl y
     varios Challengers y WTA 125, según los calendarios oficiales.
3. **La restricción coincide con la web pública de betfair.es y no con la de betfair.com**
   (EVIDENCIA fuerte, un único instante):
   - betfair.es muestra solo los 3 partidos de la Laver Cup, igual que la API;
   - betfair.com muestra además ATP Chengdu y Hangzhou, WTA Singapur y Seúl, 5 Challengers y 2
     WTA 125;
   - la web `.es` no usa App Key, así que **la restricción no depende de la Delayed Key**.
4. **El calendario explica solo una parte:**
   - la semana del 14 al 20 de septiembre **no hubo torneos ATP del circuito**, solo la Davis
     Cup (calendario oficial ATP);
   - pero sí hubo WTA 500 y 250, Challengers y WTA 125, y la semana del 21 al 27 hubo ATP 250,
     WTA 500 y 250, Challengers y WTA 125. Nada de eso llegó al catálogo `.es`.
5. **Lo que no está demostrado:**
   - la **documentación oficial de Betfair consultada no afirma** que el Exchange español tenga
     un catálogo más reducido. Solo lo afirman fuentes secundarias (un foro de 2020);
   - no se ha observado cómo es el catálogo `.es` fuera de este instante: con Grand Slams,
     Masters 1000 o en otras semanas.

---

## 3. Metodología

1. **Auditoría de código (Parte 1):** lectura de `market/client.py`, `market/collector.py`,
   `market/persistence.py`, `market/cadence.py` y `market/snapshots.py`.
2. **Calendario (Parte 2):** calendarios oficiales en PDF de la ATP (circuito y Challenger) y de
   la WTA (circuito y WTA 125), más páginas oficiales de atptour.com por torneo.
3. **API de Betfair (Parte 3):** script de diagnóstico con la configuración y el cliente del
   proyecto.
   - Solo usa operaciones `list*`, con una lista blanca en el propio script, más
     `getDeveloperAppKeys` (Accounts API, lectura).
   - Consultas progresivas: event types → competiciones → eventos → tipos de mercado → catálogo
     amplio sin ventana → `MATCH_ODDS` sin ventana → **consulta exacta de EdgeCourt** →
     `MATCH_ODDS` de las 12 h anteriores → `listMarketBook`.
   - Ejecutado el 2026-09-27 a las 07:22 UTC, en una sesión propia, cerrada al terminar. No se
     imprimió ninguna clave, token ni cabecera.
4. **Web pública (Parte 4):**
   - WebFetch de las páginas de tenis de betfair.com y betfair.es (~07:25–07:30 UTC), la de
     .com con petición de texto literal.
   - `curl` desde la máquina del proyecto (07:23 UTC).
5. **Documentación de Betfair (Parte 5):** páginas oficiales de *Login & Session Management* y
   *Application Keys*, y fuentes secundarias identificadas como tales.
6. **Histórico propio:**
   - `betfair_market` y `betfair_event` en PostgreSQL, en sesión de solo lectura;
   - journal del collector (mercados vistos por ciclo).

## 4. Periodo estudiado

- **Datos del collector:** del 2026-09-18 10:21 al 2026-09-27 08:11 CEST (auditoría Semana 1) y
  el journal hasta el 2026-09-27 ~09:25 CEST.
- **Inicios de mercado cubiertos:** del 18 al 28 de septiembre. La ventana es `now → now + 26 h`.
- **Semanas del calendario relevantes:** semana 37 (14-sep), semana 38 (21/23-sep) y el inicio de
  la semana 39 (28-sep).
- **Snapshot directo de la API y de la web:** 2026-09-27, 07:22–07:30 UTC.

---

## 5. Configuración del collector (Parte 1)

**Consulta exacta que envía EdgeCourt** en cada ciclo (`Collector._catalogue_window` +
`ReadOnlyBettingClient.list_market_catalogue`):

```json
POST https://api.betfair.com/exchange/betting/rest/v1.0/listMarketCatalogue/
{
  "filter": {
    "eventTypeIds": ["2"],
    "marketTypeCodes": ["MATCH_ODDS"],
    "marketStartTime": {"from": "<now UTC>", "to": "<now + 26 h UTC>"}
  },
  "marketProjection": ["EVENT", "COMPETITION", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
  "maxResults": 200,
  "sort": "FIRST_TO_START"
}
```

| Elemento | Valor | ¿Puede excluir torneos ATP regulares? |
|---|---|---|
| `eventTypeIds` | `["2"]` (Tennis) | No: incluye todo el tenis |
| `marketTypeCodes` | `["MATCH_ODDS"]` | Solo excluye otros tipos de mercado, no competiciones. En el snapshot, el tenis `.es` solo tenía `MATCH_ODDS` |
| `competitionIds` | **no se usa** | — |
| `marketStartTime` | `now → now + 26 h` | Excluye los partidos ya empezados o a más de 26 h. No es relevante para torneos que duran una semana |
| `inPlayOnly` | no se envía | — |
| `maxResults` | 200 | No: el máximo visto por ciclo fue de 16 mercados |
| Paginación | no hay (una llamada) | No: nunca se acercó al límite |
| `sort` | `FIRST_TO_START` | No |
| Filtros por nombre, ATP/WTA o exclusiones | **ninguno** | — |
| Normalización | `market_from_catalogue` descarta solo las entradas sin `marketId`, `event.id` o `marketStartTime` | No por competición |
| Persistencia | `_sync_catalogue` hace upsert de **todos** los mercados devueltos en `betfair_market` | Por eso `betfair_market` es un registro fiel de lo que devolvió la API |

**Conclusión de la Parte 1 (HECHO):** el código no contiene ninguna condición capaz de excluir
torneos ATP ni WTA regulares. **B descartada.**

---

## 6. Calendario real (Parte 2)

«Había torneo en el calendario» **no** implica que Betfair debiera ofrecer ese mercado.

| Semana | Torneo | Circuito / categoría | Fechas | Individual masculino | Fuente |
|---|---|---|---|---|---|
| 37 | Davis Cup Qualifiers 2.ª ronda | ITF / Davis Cup | 18–20 sep | Sí (por equipos) | ATP PDF, Wikipedia |
| 37 | Guadalajara Open | WTA 500 | semana del 14 sep | No | WTA PDF |
| 37 | SP Open (São Paulo) | WTA 250 | semana del 14 sep | No | WTA PDF |
| 37 | Caldas da Rainha, Ljubljana, Valencia | WTA 125 | semana del 14 sep | No | WTA 125 PDF |
| 37 | Szczecin, Tiburon (125); Guangzhou, Rennes (100); Biella, Phan Thiet 4 (50) | ATP Challenger | semana del 14 sep | Sí | ATP Challenger PDF |
| 37 | *(ningún ATP 250/500/1000)* | ATP Tour | — | — | ATP PDF: semana 37 = solo Davis Cup |
| 38 | Chengdu Open | ATP 250 (cuadro de 28) | 23–29 sep (qualy desde el 22) | **Sí** | atptour.com, ATP PDF |
| 38 | Hangzhou Open | ATP 250 (cuadro de 28) | 23–29 sep (qualy el 22) | **Sí** | atptour.com, ATP PDF |
| 38 | Laver Cup | exhibición por equipos | 25–27 sep | Sí | ATP PDF, Sky Sports |
| 38 | BJK Cup Finals | ITF por equipos (femenino) | semana del 21 sep | No | WTA PDF |
| 38 | Singapore Tennis Open | WTA 500 | semana del 21 sep | No | WTA PDF |
| 38 | Korea Open (Seúl) | WTA 250 | semana del 21 sep | No | WTA PDF |
| 38 | Ankara, Porto, Tolentino | WTA 125 | semana del 21 sep | No | WTA 125 PDF |
| 38 | Saint-Tropez (125); Buenos Aires, Génova, Plovdiv 4, San Diego 2 (75) | ATP Challenger | semana del 21 sep | Sí | ATP Challenger PDF |
| 39 | China Open (Pekín) | WTA 1000 | semana del 28 sep | No | WTA PDF |
| 39 | China Open (Pekín) / Japan Open (Tokio) | ATP 500 | cuadro principal desde el 30 sep (qualy en Pekín, 28–29) | Sí, **fuera de la ventana** | ATP PDF, atptour.com |

**Candidatos potenciales para EdgeCourt** (individuales masculinos, que es lo que el modelo sabe
predecir, dentro de la ventana):
- Davis Cup y Laver Cup: sí aparecieron.
- **ATP 250 Chengdu y Hangzhou** (23–27 sep): no aparecieron.
- **Challengers** de las semanas 37 y 38: no aparecieron.

WTA, WTA 125 y BJK Cup son candidatos del collector (captura todo el tenis), no del modelo.

---

## 7. Catálogo de Betfair observado (Parte 3)

Snapshot de la API del **2026-09-27 a las 07:22:03 UTC**, sesión `jurisdiction = es`, endpoint
`api.betfair.com` (HECHO; artefactos `00`–`10`):

| Consulta | Resultado |
|---|---|
| `listEventTypes` sin filtro (todo el exchange) | **2 disciplinas:** Soccer (2.995 mercados), Tennis (**3**) |
| `listCompetitions` de tenis (sin filtro de mercado ni de tiempo) | **1:** Laver Cup (3 mercados, región International) |
| `listCompetitions` de tenis con `MATCH_ODDS` | Laver Cup (3) |
| `listEvents` de tenis | 3 eventos: Zverev v Tien, Alcaraz v de Minaur, Jodar v Fritz (GB) |
| `listMarketTypes` de tenis | solo `MATCH_ODDS` (3) |
| `listMarketCatalogue` de tenis, todos los tipos, sin ventana, `maxResults = 1000` | 3 mercados |
| `listMarketCatalogue` `MATCH_ODDS`, sin ventana | 3 mercados |
| **Consulta exacta de EdgeCourt** | **3 mercados (los mismos)** |
| `MATCH_ODDS` con inicio en las 12 h anteriores (en juego o retrasados) | 0 |
| `listMarketBook` de los 3 | `OPEN`, `inplay = false`, `totalMatched = 0.0`, **`isMarketDataDelayed = true`** |

**Application Key configurada** (HECHO, vía `getDeveloperAppKeys`, sin imprimir la clave): la
clave en uso es la **Delayed** (`delayData = true`, activa). La versión Live existe pero está
**inactiva**.

## 8. Catálogo de EdgeCourt observado

`betfair_market` (todo lo que devolvió la API en 9 días):

| Competición | Mercados | Inicios |
|---|---|---|
| Davis Cup | 33 (3 importados de Parquet sin `competition_id`) | 18–20 sep |
| Billie Jean King Cup | 21 | 22–26 sep |
| Laver Cup | 11 | 25–27 sep |
| **ATP/WTA regular o Challenger** | **0** | — |

**Mercados vistos por ciclo** (journal):

| Día | Máximo de mercados | Ciclos con catálogo vacío |
|---|---|---|
| 18-sep | 16 | 0 |
| 19-sep | 15 | 0 |
| 20-sep | 9 | 412 |
| 21-sep | **0** | 1.436 (todos) |
| 22-sep | 3 | 1.091 |
| 23-sep | 3 | 1.153 |
| 24-sep | 4 | 149 |
| 25-sep | 8 | 224 |
| 26-sep | 7 | 597 |
| 27-sep | 3 | 371 (hasta ~09:25 CEST) |

Del 23 al 26 de septiembre se jugaban Chengdu y Hangzhou (cuadros individuales de 28, además de
la qualy del 22), Singapur, Seúl y 5 Challengers. El catálogo nunca pasó de 8 mercados y todos fueron de BJK
Cup o Laver Cup.

## 9. Diferencias y comparación (Parte 4)

**Web pública, mismo momento:**

| Fuente | Qué muestra | Cómo se obtuvo |
|---|---|---|
| API (sesión `.es`) | Laver Cup: 3 partidos | HECHO (JSON guardado) |
| betfair.es, página de tenis | «Copa Laver»: los mismos 3 partidos | WebFetch, resumen automático |
| betfair.com, página de tenis | Laver Cup (3) + **ATP Chengdu 2026**, **ATP Hangzhou 2026** (partidos en juego y del día, p. ej. Muller v Davidovich Fokina, Marozsan v Jacquet, Medvedev v Wong) + **WTA Singapore 2026**, **WTA Seoul 2026** + Challengers de Saint-Tropez, Buenos Aires, Génova, Plovdiv y Porto + WTA Ankara y WTA Porto + BJK Cup + futuros de Grand Slam 2027 | WebFetch, pidiendo texto literal. La lista coincide con los calendarios oficiales independientes (§6) |
| `curl` desde la máquina del proyecto (IP española) a betfair.com | **Redirige a betfair.es.** El HTML es un armazón JavaScript sin contenido | HECHO |

**Tabla de comparación por competición:**

| Competición | Tipo | Visible en el calendario | Visible en la API de Betfair (sesión `.es`) | Visible en EdgeCourt | `MATCH_ODDS` | Motivo de exclusión |
|---|---|---|---|---|---|---|
| Davis Cup Qualifiers R2 | Masc. por equipos | Sí (18–20 sep) | Sí (histórico: `betfair_market`) | Sí (33) | Sí | — |
| Billie Jean King Cup Finals | Fem. por equipos | Sí (semana del 21) | Sí (histórico) | Sí (21) | Sí | — |
| Laver Cup | Masc. exhibición | Sí (25–27 sep) | Sí (histórico + snapshot) | Sí (11) | Sí | — |
| ATP 250 Chengdu | ATP individual | Sí (23–29 sep) | **No** (nunca en `betfair_market`; ausente del snapshot) | No | NO VERIFICADO en `.es` | No lo ofrece la API a esta sesión. En betfair.com sí aparece |
| ATP 250 Hangzhou | ATP individual | Sí (23–29 sep) | **No** | No | NO VERIFICADO en `.es` | Ídem |
| WTA 500 Singapur | WTA individual | Sí (semana del 21) | **No** | No | NO VERIFICADO en `.es` | Ídem |
| WTA 250 Seúl | WTA individual | Sí (semana del 21) | **No** | No | NO VERIFICADO en `.es` | Ídem |
| WTA 500 Guadalajara | WTA individual | Sí (semana del 14) | **No** (histórico) | No | NO VERIFICADO | No lo devolvió la API. En .com: NO VERIFICADO (no se observó esa semana) |
| WTA 250 São Paulo | WTA individual | Sí (semana del 14) | **No** (histórico) | No | NO VERIFICADO | Ídem |
| ATP Challengers, semana 37 (6) | Challenger | Sí | **No** (histórico) | No | NO VERIFICADO | Ídem |
| ATP Challengers, semana 38 (5) | Challenger | Sí | **No** | No | NO VERIFICADO | No lo ofrece la API. 4 de ellos aparecen en betfair.com (snapshot) |
| WTA 125, semanas 37–38 | WTA 125 | Sí | **No** | No | NO VERIFICADO | Ídem (Ankara y Porto aparecen en betfair.com) |
| ATP 500 Pekín / Tokio | ATP individual | Sí, pero desde el 30 sep | NO VERIFICADO | No | NO VERIFICADO | Fuera de la ventana temporal (E, comportamiento correcto) |
| WTA 1000 Pekín | WTA individual | Sí (semana del 28) | **No** (snapshot del 27, 07:22 UTC) | No | NO VERIFICADO | Podía no estar publicado aún o no ofrecerse en `.es`. NO VERIFICADO |

«Visible en la API = No» se apoya en dos hechos:
- `betfair_market` guarda **todo** lo que devolvió la API durante 9 días;
- el snapshot directo del 27-sep devolvió el catálogo de tenis completo, sin filtros.

---

## 10. Jurisdicción `.es` (Parte 5)

| Tipo | Afirmación | Fuente |
|---|---|---|
| **HECHO** | La cuenta se autentica con `BETFAIR_JURISDICTION = es` (endpoint de identidad `.es`) y consulta la Betting API en `api.betfair.com` | Código, logs (`"jurisdiction": "es"`) |
| **HECHO** | Con esa sesión, todo el exchange visible son 2 disciplinas, y el tenis se reduce a 3 mercados | Snapshot de la API, 27-sep 07:22 UTC |
| **HECHO** | Desde una IP española, betfair.com redirige a betfair.es | `curl`, 27-sep 07:23 UTC |
| **HECHO** | La clave es Delayed (`delayData = true`) y los libros llegan con `isMarketDataDelayed = true` | `getDeveloperAppKeys`, `listMarketBook` |
| **EVIDENCIA oficial** | «The session expiry time is currently **20 minutes** on the Italian & Spanish Exchange». Los endpoints de `keepAlive` y `logout` son específicos: `identitysso.betfair.es` | [Login & Session Management](https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687869/Login+Session+Management) |
| **EVIDENCIA oficial** | Retraso de la Delayed Key: «variable between 1-180 second snapshots». En la tabla *Delay & Live Application Keys Overview*: «Total Matched by Selection»: Delayed «Not Available», y «Total Matched by Market»: Delayed «Yes». La misma página dice también en prosa que la Delayed Key «does not return traded volume data 'totalMatched' or EX_ALL_OFFERS»; la tabla precisa que la limitación es por selección. **No menciona** ninguna restricción de catálogo | [Application Keys](https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687105/Application+Keys) |
| **EVIDENCIA oficial** | «**Read-only access using the Live App Key isn't permitted**» | ídem |
| **EVIDENCIA** (resumen de un buscador de la página oficial «Betting on Spanish Exchange», **no leída directamente**: redirige) | Tras el login `.es`, «any further API requests should be sent to the UK Exchange endpoints», que es lo que hace EdgeCourt | Búsqueda web. La página original no se pudo abrir |
| **EVIDENCIA fuerte** (un instante) | La web pública betfair.es muestra el mismo catálogo reducido que la API. betfair.com muestra los torneos regulares. La web `.es` no usa App Key | WebFetch de ambas páginas (§9) |
| **EVIDENCIA secundaria** (foro, usuarios corrientes, 17-ene-2020) | «It's a completely separate exchange only open to Spanish residents … and they only have a limited number of markets»; «these cut-off exchanges have lower liquidity and much wider spreads» | [Bet Angel forum](https://forum.betangel.com/viewtopic.php?t=11112) |
| **HIPÓTESIS** | El Exchange español es un pool de liquidez separado, y eso explicaría también los spreads de la auditoría (favorito ~13 %) | Coherente con la evidencia secundaria y con los datos. **No demostrado** con documentación oficial |
| **HIPÓTESIS** | La oferta `.es` la fija Betfair España (o el marco regulatorio español) seleccionando eventos, lo que explicaría 2 disciplinas y solo algunos eventos «grandes» | Sin fuente oficial |
| **NO VERIFICADO** | Si el catálogo `.es` incluye Grand Slams, Masters 1000 u otros ATP en otras semanas | Hace falta observación longitudinal |
| **NO VERIFICADO** | Si una cuenta de otra jurisdicción, con la misma clave o una equivalente, vería el catálogo global | No hay otra cuenta. **No se recomienda eludir la jurisdicción** |

**Lo que la documentación oficial NO permite demostrar:** en las páginas oficiales consultadas
no consta que el Exchange español tenga un catálogo distinto o más reducido. La atribución a la
jurisdicción se apoya en la observación directa (API = web `.es` ≠ web .com) y en fuentes
secundarias, no en una declaración de Betfair.

---

## 11. Hipótesis descartadas

| Hipótesis | Estado | Razón |
|---|---|---|
| **B** Filtros de EdgeCourt | **Descartada** | No hay filtros de competición, nombre ni categoría. La consulta de EdgeCourt devuelve lo mismo que el catálogo sin filtros (3 = 3) |
| **C** Filtrado por `marketType` / `eventType` / competición | **Descartada** | Sin filtro de tipo de mercado también salen 3 mercados. `eventTypeIds = 2` es el tenis completo y no hay filtro de competición |
| **E** Rango temporal | **Descartada** como causa principal | Sin ventana temporal salen los mismos 3 mercados. Solo explica la ausencia de Pekín/Tokio ATP (desde el 30-sep), que es correcta |
| **F** `maxResults` / paginación | **Descartada** | Máximo de 16 mercados por ciclo frente a un límite de 200. Con `maxResults = 1000`, 3 mercados |
| **Delayed Key como causa de la restricción** | **Descartada con evidencia** | La web pública `.es`, sin App Key, muestra la misma restricción. La documentación de la Delayed Key no menciona límites de catálogo |
| **H** Diferencia entre la web y la API para `.es` | **Descartada** | Web `.es` = API (Laver Cup, 3) |

## 12. Hipótesis que siguen abiertas

| Hipótesis | Estado |
|---|---|
| **A** Calendario | **Explica parcialmente** la semana 37 (sin ATP Tour). No explica la ausencia de WTA, Challengers ni ATP 250 de la semana 38 |
| **D/G** Jurisdicción `.es` | **Apoyada por evidencia empírica fuerte.** No documentada oficialmente. Falta saber su alcance temporal (qué ofrece `.es` en otras semanas) |
| **I** Otros | Posible restricción a nivel de cuenta, además de la jurisdicción: **NO VERIFICADO**. Es poco probable como explicación principal, porque la web `.es` anónima muestra lo mismo |

## 13. Limitaciones

- La comparación API / web `.es` / web .com es de **un único instante** (27-sep, 07:22–07:30
  UTC). Para las semanas anteriores solo hay el histórico `.es` (lo que devolvió la API), no una
  foto de .com.
- Las páginas web se leyeron con WebFetch, que resume con un modelo automático. La lista de .com
  se validó contra calendarios oficiales independientes, pero **no** se guardó el HTML
  renderizado: el `curl` desde España redirige y devuelve un armazón JavaScript.
- La página oficial «Betting on Spanish Exchange» no se pudo abrir (redirige). Su contenido se
  conoce solo por el extracto de un buscador.
- La muestra coincide con una semana especial (Davis Cup y Laver Cup). No se sabe si el catálogo
  `.es` es más amplio en Grand Slams o Masters.

## 14. Hallazgos incidentales

1. **`totalMatched` con Delayed Key: coherente con la documentación.** Según la tabla oficial
   *Delay & Live Application Keys Overview*, el total casado **por mercado** sí está disponible
   con la Delayed Key y el total casado **por selección** no. Los datos lo reflejan:
   - `market_observation.total_matched` fue > 0 en 309 de 1.497 observaciones (10 mercados,
     máximo 1.406);
   - `runner_price.runner_total_matched` fue 0 en las 1.967 filas.

   Consecuencias:
   - las cifras de `total_matched` (por mercado) de la auditoría de la Semana 1 son válidas en
     cuanto a su disponibilidad;
   - el volumen casado **por runner** no está disponible con esta clave.

   > **Corrección (2026-09-27, antes del primer commit de este documento):** la versión inicial
   > presentaba esto como una «discrepancia sin resolver», basándose solo en la frase en prosa de
   > la documentación. La tabla de la misma página la resuelve.
2. **Los datos son retrasados** (`isMarketDataDelayed = true`, 1–180 s). El esquema no guarda ese
   flag.
3. **3.5-A confirmado por la documentación oficial:** en el Exchange español la sesión caduca a
   los 20 min, que es exactamente lo observado.
4. **La Live Key no admite uso de solo lectura** («Read-only access using the Live App Key isn't
   permitted»). Un proyecto que no apuesta no puede apoyarse en ella para obtener datos en tiempo
   real.
5. **`edgecourt collector health`** muestra la hora local etiquetada como «UTC» (p. ej. «09:00:00
   UTC» para una observación de las 07:00 UTC). Es un defecto de presentación, sin impacto en los
   datos. No se ha corregido.

---

## 15. Conclusión

**F) Combinación:**
- **A) calendario, en parte:** la semana del 14-sep no tuvo ATP Tour;
- **D) efecto de jurisdicción, con evidencia empírica fuerte pero sin confirmación oficial:**
  el catálogo que Betfair sirve a la cuenta `.es` (API y web) excluyó ATP 250, WTA 500/250,
  Challengers y WTA 125 que sí estaban en el calendario y, al menos el 27-sep, en betfair.com.

**B) descartado:** EdgeCourt no tiene ningún bug ni filtro que pierda mercados. La Delayed Key no
explica la restricción del catálogo.

**E) evidencia insuficiente** para afirmar cómo es el catálogo `.es` en otras semanas.

## 16. Impacto sobre EdgeCourt (Parte 7)

| Área | Implicación |
|---|---|
| **Objetivo ATP prematch** ([DEC-002](../DECISIONS.md#dec-002)) | Con la cuenta actual, **los partidos ATP regulares no se pudieron observar** en toda la muestra. Si el catálogo `.es` no los ofrece nunca o casi nunca, el objetivo no es alcanzable con esta fuente de mercado. Requiere una **decisión del responsable del proyecto**, que no se toma aquí |
| **Collector** | Funciona correctamente y no pierde mercados. No necesita cambios por 3.5-C. Podría registrarse en el futuro `isMarketDataDelayed`, pero no se implementa |
| **Phase 3.5-B (closing price)** | Su valor depende de la decisión anterior. En `.es` hay pocos mercados, liquidez muy baja (auditoría) y **datos retrasados 1–180 s**, justo en el tramo donde se forma el cierre. Un CLV sobre `.es` sería, como mucho, orientativo. **Conviene decidir la fuente de mercado antes de invertir en 3.5-B** |
| **Phase 8b (identity resolution)** | Con el catálogo actual solo habría jugadores de Davis Cup y Laver Cup (y de BJK, fuera del modelo). El volumen es demasiado bajo para justificar la fase ahora |
| **Phase 4 (modelos)** | Los modelos no dependen del mercado para entrenarse ni evaluarse en calidad probabilística. Pero sin mercado ATP observable, su evaluación **económica** (Value, CLV, ROI) no es posible hacia delante |
| **Evaluación de value** | Con la fuente actual, el benchmark de mercado (MODEL 0) no existe para los partidos que el modelo sabe predecir. Además, con la Delayed Key el volumen casado solo está disponible por mercado (no por runner), los precios llegan con 1–180 s de retraso y la Live Key no admite uso de solo lectura |

## 17. Próximos pasos propuestos (no implementados)

1. **Observación longitudinal del catálogo `.es`** (3.5-D, sin cambios de código): repetir el
   snapshot de solo lectura en semanas con ATP 500/1000 (Pekín/Tokio desde el 30-sep, Shanghái
   desde el 7-oct) para saber si `.es` ofrece alguna vez ATP regular.
2. **Decisión del responsable del proyecto** sobre la fuente de mercado, con las alternativas que
   haya y **siempre dentro de la legalidad aplicable a la cuenta**. Por ejemplo: aceptar el
   alcance `.es`, usar Betfair Historical Data (de pago) para investigación retrospectiva, u
   otras. No se evalúan aquí.
3. Revisar la prioridad de 3.5-B a la luz del punto 2.

## 18. Fuentes consultadas

**Oficiales**
- ATP, calendario 2026 (PDF, publicado el 18-ago-2026; incluye circuito y Challenger):
  `https://www.atptour.com/-/media/files/calendar-pdfs/2026/2026-27-atp-challenger-calendar-as-of-18-aug-2026.pdf`
- WTA, calendario 2026 (PDF; circuito a 30-jun-2026, WTA 125 a 7-jul-2026):
  `https://wtafiles.wtatennis.com/pdf/calendar/calendar.pdf`
- atptour.com: [Chengdu Open 2026](https://www.atptour.com/en/news/chengdu-atp-250-2026-history-draw-schedule),
  [Hangzhou Open 2026](https://www.atptour.com/en/news/hangzhou-atp-250-2026-history-draw-schedule),
  [China Open 2026](https://www.atptour.com/en/news/beijing-atp-500-2026-history-draw-schedule),
  [Laver Cup 2026](https://www.atptour.com/en/news/laver-cup-2026-history-draw-schedule)
- Betfair Developer Docs: [Login & Session Management](https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687869/Login+Session+Management),
  [Application Keys](https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687105/Application+Keys)
- Web pública: `https://www.betfair.com/exchange/plus/tennis` y
  `https://www.betfair.es/exchange/plus/es/tenis-apuestas-2` (27-sep, ~07:25 UTC)

**Secundarias**
- [2026 Davis Cup Qualifiers second round (Wikipedia)](https://en.wikipedia.org/wiki/2026_Davis_Cup_Qualifiers_second_round)
- [Tennis Connected, orden de juego del 23-sep-2026](https://tennisconnected.com/atp-wta-2026-daily-schedule-of-play-for-chengdu-hangzhou-singapore-and-seoul-for-wednesday-september-23/)
- [Sky Sports, Laver Cup 2026](https://www.skysports.com/tennis/news/13588657/laver-cup-2026-schedule-dates-format-players-and-how-to-watch-or-stream-team-europe-vs-team-world-at-londons-o2-on-sky-sports)
- [Bet Angel forum, «Betfair Spain now has an exchange?» (2020)](https://forum.betangel.com/viewtopic.php?t=11112)
- [Betfair Developer Forum, «What data is available with a delayed key?» (2019)](https://forum.developer.betfair.com/forum/sports-exchange-api/exchange-api/30254-what-data-is-available-with-a-delayed-key)

## 19. Artefactos

En [`2026-09-atp-wta-catalogue/`](2026-09-atp-wta-catalogue/):
- `00_report.json`: resumen del snapshot.
- `01`–`10`: respuestas completas de cada consulta (sin datos sensibles).
- `SCRIPTS.md`: scripts literales.

# Collector de Betfair

Proceso de larga duración que observa mercados `MATCH_ODDS` de tenis y escribe lo observado en
PostgreSQL. **Solo lectura frente a Betfair.** No decide nada ni apuesta.

- **Configuración de credenciales y certificado:** [`BETFAIR_SETUP.md`](../BETFAIR_SETUP.md).
- **Garantía de solo lectura:** [DEC-001](../DECISIONS.md#dec-001) y
  [DEC-009](../DECISIONS.md#dec-009).
- **Almacenamiento:** [`POSTGRESQL.md`](POSTGRESQL.md).

## Estado (2026-09-27)

| | |
|---|---|
| Servicio | `edgecourt-collector.service`, en operación desde el 2026-09-18. Reinicios controlados: 2026-09-27 (keepAlive de 3.5-A) y 2026-09-30 (aislamiento por mercado). **Sin persistir del 30-sep 23:40 UTC al 5-oct 13:17 UTC** por el [incidente de particiones](../audits/2026-10-partition-timezone-incident.md), ya corregido |
| Uptime en los últimos 7 días | 10.054 ciclos, 0 fallos, intervalo P50 60,13 s y P99 60,57 s |
| Jurisdicción | **Sesión española**, verificada: `BETFAIR_JURISDICTION = es`, login, `keepAlive` y `logout` en `identitysso.betfair.es`; los logs registran `"jurisdiction": "es"` |
| Betting API | `https://api.betfair.com/exchange/betting/rest/v1.0`: la global, también con sesión `.es`. Verificado que funciona en `46fae68`. Sirve el catálogo que corresponde a la cuenta `.es` ([investigación 3.5-C](../investigations/2026-09-atp-wta-catalogue.md)) |
| Application Key | **Verificado el 2026-09-27** con `getDeveloperAppKeys`, sin exponer la clave: la **Delayed** está **activa** (`delayData = true`) y la **Live**, **inactiva** |
| Retraso de los datos | **Verificado:** los libros llegan con `isMarketDataDelayed = true`. Según la documentación oficial, la Delayed Key da «1-180 second snapshots» |
| Volumen casado con la Delayed Key | Según la tabla oficial *Delay & Live Application Keys Overview*: por **mercado**, sí; por **selección**, no. Coincide con los datos: `runner_total_matched` siempre es 0 |
| Duración de la sesión | **20 minutos en el Exchange español**, verificado en la documentación oficial (*Login & Session Management*) y coincidente con lo observado. Se renueva con keepAlive cada 15 min |
| Catálogo | El catálogo `.es` observado es muy reducido (27-sep: solo Laver Cup en tenis). Que Betfair.es tenga oficialmente menos mercados que Betfair.com está **NO VERIFICADO oficialmente**: es una observación empírica ([3.5-C](../investigations/2026-09-atp-wta-catalogue.md)) |
| Problemas abiertos | [Phase 3.5](../phases/PHASE_03_5_MARKET_VALIDATION.md): closing price (B) y catálogo `.es` (C, decisión pendiente; en C2 aparece ATP de forma parcial). El `keepAlive` (A) está **DONE**. **Abierto:** `systemd` puede ver el servicio `active` aunque el collector no persista (ver el [incidente §11](../audits/2026-10-partition-timezone-incident.md)) |

## Ciclo

Cada `COLLECTOR_INTERVAL_SECONDS` (60 s por defecto; en la práctica, ~60,16 s por ciclo):

1. **Catálogo.** `listMarketCatalogue` con:
   - `eventTypeIds=["2"]` (tenis) y `marketTypeCodes=["MATCH_ODDS"]`;
   - ventana `now → now + 24 h + 2 h` y `maxResults = 200`;
   - `sort=FIRST_TO_START`.

   **No hay filtro de competición**, así que entran también WTA y dobles. El catálogo se
   sincroniza con `betfair_event`, `betfair_market` y `betfair_runner` en su propia transacción. En
   esa misma transacción se garantizan las **particiones mensuales UTC del mes actual y del
   siguiente** (`ensure_partitions`, [DEC-019](../DECISIONS.md#dec-019)).
2. **Estado.** Se lee de PostgreSQL por mercado: si mostró precios o liquidez, la última
   observación y los hitos ya capturados. Así la planificación sobrevive a reinicios.
3. **Planificación** (`market/cadence.py::plan_captures`). Como mucho, una captura por mercado y
   ciclo:
   - si vence un hito no capturado, se captura el hito (tiene prioridad);
   - si no, se aplica la cadencia adaptive, solo si el mercado ya mostró precios o liquidez;
   - con `minutes_left < 0` según la hora publicada, no se captura nada: solo pre-partido.
4. **Precios.** `listMarketBook` en lotes de 40, con `EX_BEST_OFFERS`, profundidad 3 y
   `virtualise=true`. Se agrupa por etiqueta.
5. **Escritura.** Una transacción por mercado (`save_observation`), idempotente por
   `(market_id, capture_key, observed_at)`. Desde el 2026-09-30, si PostgreSQL rechaza los datos
   de un mercado (`DataError`, `IntegrityError`), solo se deshace ese mercado: el ciclo continúa y
   el `market_id` queda en `failed_markets`
   ([incidente](../audits/2026-09-spread-overflow-incident.md)).

## Política de captura ([DEC-006](../DECISIONS.md#dec-006))

| Hito | Objetivo (min antes) | Tolerancia |
|---|---|---|
| 24h | 1.440 | ±45 |
| 12h | 720 | ±30 |
| 6h | 360 | ±20 |
| 1h | 60 | ±8 |
| 10m | 10 | ±3 |
| close | 2 | ±2 |

| Cadencia adaptive | a menos de | cada |
|---|---|---|
| tramo 1 | 360 min | 30 min |
| tramo 2 | 90 min | 10 min |
| tramo 3 | 30 min | 5 min |
| tramo 4 | 10 min | 1 min |

`capture_key` adaptive = `a:YYYYMMDDHHMM`, alineada a la rejilla del intervalo.

**Defecto conocido** (auditoría de la Semana 1, §7):
- Los hitos se calculan contra la hora de inicio **publicada en ese momento** y no se repiten.
- Cuando Betfair retrasa `market_start_time`, el `close` queda lejos del inicio real: 45 de 58
  casos a más de 5 min.
- El mercado vuelve a entrar en la ventana de 1 min una y otra vez.

Se rediseña en Phase 3.5-B.

## Sesión y errores

- **Login** no interactivo por certificado, en el endpoint de identidad de la jurisdicción. Tanto
  `keepAlive` como `logout` van al mismo dominio.
- **keepAlive preventivo** (`market/auth.py`, Phase 3.5-A, desde el 2026-09-27):
  - `keep_alive_interval(jurisdiction)` da **15 min para `es` e `it`**, cuyas sesiones caducan a
    los 20 min según la documentación oficial (`SESSION_TIMEOUT_BY_JURISDICTION`). Las demás
    jurisdicciones siguen en `KEEP_ALIVE_INTERVAL` = 1 h. `SESSION_MAX_AGE` = 8 h, sin cambios.
  - Se evalúa al inicio de cada llamada a la API (~1 por ciclo): no se envía en cada ciclo, sino
    ~4 veces por hora.
  - Un keepAlive correcto registra `INFO "sesion renovada (keepAlive)"`, solo con la
    jurisdicción; el token nunca se registra.
  - Si falla (red, `status` ≠ `SUCCESS` o respuesta no JSON), se reautentica. Un fallo de
    keepAlive y de login a la vez es un error recuperable: el ciclo falla, el collector sigue y
    el siguiente ciclo reintenta.
  - **Verificado en producción:** keepAlive real aceptado el 2026-09-27 a las 08:15:02 UTC y
    ninguna caducidad al pasar el minuto 20
    ([validación](../audits/2026-09-session-keepalive-validation.md)).
- **Segunda barrera, sin cambios:** si una llamada devuelve `INVALID_SESSION_INFORMATION`, el
  cliente invalida la sesión y reintenta con un login nuevo **dentro de la misma llamada**. Antes
  de 3.5-A ocurría cada ~20 min (se recuperaba en el 100 % de los casos). Ahora debería ser
  excepcional.
- **Reintentos:**
  - backoff exponencial ante errores de red, `TOO_MANY_REQUESTS` y errores 5xx;
  - `INVALID_INPUT_DATA` aborta;
  - un fallo de configuración aborta de inmediato, sin reintentar en bucle.
- **Tras 5 fallos consecutivos**, espera larga de 300 s.
- **Apagado:** SIGTERM o SIGINT → termina el ciclo, cierra la sesión en Betfair y libera el
  bloqueo.

## Operación

```bash
systemctl status edgecourt-collector            # estado del servicio
journalctl -u edgecourt-collector -f            # logs JSON en vivo
uv run edgecourt collector health               # sale con código 1 si hay avisos; instantes en UTC (Z)
uv run edgecourt collector status               # cobertura por etiqueta (desde PostgreSQL)
uv run edgecourt betfair check                  # credenciales y lectura, sin escribir nada
```

- **`active` no significa «persistiendo».** `systemd` solo ve si el proceso vive, y un fallo
  determinista de ciclo nunca lo termina (el bucle absorbe cada excepción). `collector health` es
  quien detecta que no se persiste, pero hoy **no lo ejecuta ni lo vigila nadie**. Propuesta en el
  [incidente §11](../audits/2026-10-partition-timezone-incident.md).
- **Un único collector:** advisory lock de PostgreSQL (`db/locks.py`). Si se lanza un segundo a
  mano, sale con código 6 e indica el PID que tiene el bloqueo.
- **Logs:** bajo systemd, JSON por stdout al journal (`SyslogIdentifier=edgecourt-collector`), y
  además `logs/collector.log`. Los secretos se redactan por valor y por patrón.
- **Unidad:** `deploy/edgecourt-collector.service`, instalada con `scripts/install_service.sh`,
  que muestra la unidad y pide confirmación. La unidad real corre con el usuario del proyecto,
  desde el repositorio, y lee el `.env` del proyecto. **`deploy/README.md` describe todavía otro
  despliegue** (ver [problemas conocidos](../PROJECT_STATUS.md#problemas-conocidos)).

## Volumen

`estimate_daily_observations()` estima ~35 observaciones como máximo por mercado (6 hitos + 29
adaptive). En la Semana 1, los mercados adaptive tuvieron **54 observaciones de mediana (máximo
91)**, por los retrasos de `market_start_time`. El volumen total sigue siendo pequeño: 1.497
filas en 9 días.

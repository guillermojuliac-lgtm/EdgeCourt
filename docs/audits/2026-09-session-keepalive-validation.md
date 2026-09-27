# Validación del keepAlive de sesión (Phase 3.5-A)

> **Snapshot inmutable** del 2026-09-27. Convención en [`docs/README.md`](../README.md).

| Campo | Valor |
|---|---|
| Fecha | 2026-09-27 |
| Fase | [Phase 3.5-A](../phases/PHASE_03_5_MARKET_VALIDATION.md) |
| Antecedentes | [Auditoría Semana 1](2026-09-week1-market-audit.md) §6.1 · [Investigación 3.5-C](../investigations/2026-09-atp-wta-catalogue.md) §10 |
| Código cambiado | `src/edgecourt/market/auth.py` (solo gestión de sesión) |
| Tests | `tests/test_betfair_keepalive.py` (nuevo) y un test más en `tests/test_betfair_collector.py` |
| Scripts y salidas | [`2026-09-session-keepalive-validation/SCRIPTS.md`](2026-09-session-keepalive-validation/SCRIPTS.md) |

## 1. Problema

El collector registraba `INVALID_SESSION_INFORMATION` **cada 20,05 min** (desviación típica de
0,007 min): 503 veces en los 7 días de la auditoría y 624 desde el 18-sep. Tras cada una
reautenticaba. No se perdían datos (503 de 503 ciclos completados), pero cada caducidad costaba
una llamada fallida y un login.

## 2. Causa

- La documentación oficial de Betfair dice: «The session expiry time is currently **20 minutes**
  on the Italian & Spanish Exchange». Añade que el keepAlive debe llamarse dentro de ese plazo
  (*Login & Session Management*).
- EdgeCourt usaba `KEEP_ALIVE_INTERVAL = 1 h` para todas las jurisdicciones. En la cuenta `.es`,
  la sesión caducaba siempre antes del primer keepAlive.
- El comentario del código atribuía a Betfair «4 horas de inactividad», sin referencia. Esa cifra
  no aplica al Exchange español.

## 3. Comportamiento anterior

```
login ─► (1 h sin keepAlive) ─► a los 20 min Betfair invalida la sesión
      ─► siguiente llamada: INVALID_SESSION_INFORMATION
      ─► _call invalida la sesión ─► reintento con login nuevo ─► ciclo OK
      (se repite cada ~20 min: ~72 reautenticaciones al día)
```

- `SessionManager.current()` se llama antes de cada petición a la API (~1 por ciclo de 60 s).
  Si `needs_keep_alive` (≥ 1 h), llama a `keep_alive()`; si este falla, reautentica.
- Un keepAlive correcto no dejaba traza en el log.
- Una respuesta de keepAlive que no fuera JSON lanzaba `ValueError` sin capturar: el ciclo fallaba
  (el bucle lo absorbía y el proceso seguía vivo).

## 4. Cambio

Solo en `src/edgecourt/market/auth.py`:

| Elemento | Antes | Después |
|---|---|---|
| Intervalo de keepAlive | `KEEP_ALIVE_INTERVAL = 1 h` para todo | `keep_alive_interval(jurisdiction)`: **15 min para `es` e `it`**; 1 h para el resto (sin cambios) |
| Caducidad documentada | — | `SESSION_TIMEOUT_BY_JURISDICTION = {es: 20 min, it: 20 min}`, con la cita oficial en el código |
| `Session.needs_keep_alive` | constante global | usa el intervalo de la jurisdicción de la sesión |
| Respuesta no JSON del keepAlive | `ValueError` sin capturar | keepAlive fallido controlado (`WARNING`) → reautenticación |
| Payload que no es un objeto | posible `AttributeError` | tratado como rechazo |
| keepAlive correcto | sin traza | `INFO "sesion renovada (keepAlive)"`, solo con la jurisdicción. El token de la respuesta **nunca** se registra |

**Sin cambios:**
- La reautenticación ante `INVALID_SESSION_INFORMATION` sigue siendo la segunda barrera.
- No se tocan `SESSION_MAX_AGE` (8 h), el descubrimiento, la cadencia, el esquema, la
  configuración ni las App Keys.

**Por qué 15 minutos:**
- El keepAlive solo se evalúa cuando hay una llamada a la API, es decir, una por ciclo de ~60 s.
  Con 15 min se envía como tarde hacia el minuto 16 y quedan ~4 min de margen para absorber
  ciclos lentos o fallidos.
- 10 min añadiría un 50 % más de llamadas sin necesidad.
- 18 min dejaría solo ~2 min de margen, que un solo ciclo con backoff podría consumir.

**Por qué no se añadió una variable de entorno:**
- No existía configuración previa para esto.
- La caducidad es un dato fijado por Betfair según la jurisdicción, no una preferencia de
  operación.
- Una constante documentada con su fuente es explícita y no amplía la superficie de
  configuración.
- Si Betfair cambiara la caducidad, se cambia la constante y el test que la fija.

## 5. Tests

**Añadidos en `tests/test_betfair_keepalive.py`** (14; los marcados con ★ son `critical`):

| Test | Demuestra |
|---|---|
| ★ `test_segregated_exchange_keep_alive_is_below_the_documented_timeout[es/it]` | intervalo < 20 min, con ≥ 3 min de margen |
| `test_other_jurisdictions_keep_the_default_interval` | `.com` y `.com.au` no cambian |
| `test_spanish_session_needs_keep_alive_before_twenty_minutes` | a los 14 min no toca; a los 15 sí |
| ★ `test_spanish_session_is_renewed_before_it_expires_over_an_hour` | una hora simulada de ciclos de 60 s: ningún tramo sin renovar llega a 20 min y nunca hay re-login |
| ★ `test_keep_alive_is_not_sent_on_every_cycle` | 61 llamadas en una hora → solo 4 keepAlive |
| `test_successful_keep_alive_preserves_the_session` | misma sesión, sin login nuevo |
| `test_keep_alive_does_not_reveal_the_token_in_logs` | se registra la renovación, nunca el token |
| `test_rejected_keep_alive_falls_back_to_relogin` | `status: FAIL` → reautenticación |
| `test_non_json_keep_alive_response_is_handled` / `test_non_json_success_status_is_handled` | un 502 HTML o un 200 que no es JSON no lanzan excepción → re-login |
| ★ `test_keep_alive_and_relogin_failure_is_recoverable` | sin red, keepAlive y login fallan con error recuperable; al volver la red, se recupera |
| ★ `test_invalid_session_still_triggers_recovery` | `INVALID_SESSION_INFORMATION` → invalidar → login (segunda barrera intacta) |
| ★ `test_session_management_only_talks_to_identity_and_read_endpoints` | la gestión de sesión solo contacta con los hosts de identidad (login, keepAlive, logout) |

**Añadido en `tests/test_betfair_collector.py`:**
★ `test_session_renewal_failure_does_not_kill_the_process`. Un `AuthenticationError` en cada
ciclo no detiene el bucle, a diferencia de `MissingCredentialsError`.

**Resultados:**
- Suite completa: **472 passed** (antes 457; +15).
- Críticos: **151 passed** (antes 143; +8).
- Los tests de integración usan la base de tests, distinta de la operativa.
- `ruff check .` y `ruff format --check .` limpios.
- La barrera de solo lectura (`tests/test_no_real_betting_surface.py`) sigue en verde.

## 6. Validación real contra Betfair

**a) Sonda puntual** (sesión propia y efímera, 07:57 UTC): login `.es` → keepAlive real →
**aceptado** (`status = SUCCESS`, `last_keep_alive` avanzado) → lectura correcta con la misma
sesión → logout.

**b) Sonda de extensión** (sesión propia, lecturas **sin reautenticación automática**):

| t (min) | Hora UTC | Acción | Resultado |
|---|---|---|---|
| 0 | 07:58:06 | login + lectura | OK |
| 15 | 08:13:06 | keepAlive | **aceptado** |
| 21 | 08:19:06 | lectura sin reautenticar | **OK** (6 eventos) |
| 24 | 08:22:06 | lectura sin reautenticar | **OK** (6 eventos) |

Sin keepAlive la sesión habría caducado en el minuto 20. **Esto demuestra que Betfair acepta el
keepAlive y que extiende la sesión española.**

**c) Servicio en producción:**
- Reinicio controlado (`systemctl restart`) a las 07:59:59 UTC: SIGTERM, cierre de sesión,
  bloqueo liberado y arranque con el PID 4066628.

| Hora UTC | Evento en el journal |
|---|---|
| 07:57:09 | *(código antiguo)* `INVALID_SESSION_INFORMATION` → reautenticación: la última |
| 07:59:59 | parada limpia (12.564 ciclos en el run anterior) |
| 08:00:00 | `collector iniciado` → `sesion iniciada` (`es`) |
| **08:15:02** | **`sesion renovada (keepAlive)` (`es`)** |
| 08:00–08:22 | 23 ciclos completados, **0 `INVALID_SESSION_INFORMATION`**, 0 errores |

Estado a las 08:22 UTC: `active`, `NRestarts = 0`, `edgecourt collector health` → **OK**
(código de salida 0).

## 7. Comportamiento nuevo

```
login ─► 15 min ─► keepAlive (aceptado) ─► sesión extendida ─► 15 min ─► keepAlive ─► …
                         │
                         └─ si falla (red, rechazo, no JSON) ─► reautenticación
INVALID_SESSION_INFORMATION en una llamada ─► invalidar ─► login   (segunda barrera, intacta)
```

Esperado en régimen estable: **~96 keepAlive al día** (4 por hora) en lugar de ~72 pares de
llamada fallida + login.

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Betfair cambia la caducidad de las sesiones `.es` | Si baja de ~16 min, vuelve el patrón antiguo y la reautenticación lo cubre sin pérdida de datos. Se detectaría por la reaparición de `INVALID_SESSION_INFORMATION` periódicos |
| Varios ciclos seguidos con backoff (esperas de hasta 300 s) retrasan el keepAlive más allá de 20 min | La sesión caduca y la reautenticación la recupera. Es el mismo comportamiento que antes, solo en casos excepcionales |
| Exposición del token en el log de renovación | Solo se registra la jurisdicción. Hay un test que comprueba que el token no aparece |
| Ruta de apuestas | Ninguna: solo cambian los intervalos y el manejo de errores del keepAlive. La barrera de solo lectura sigue en verde y hay un test nuevo que limita los hosts de la gestión de sesión |

## 9. Resultado

**Problema resuelto y verificado en producción.** La sesión española se mantiene con keepAlive
preventivo cada 15 min. Betfair lo acepta y extiende la sesión (sonda: válida a los 21 y 24 min).
El servicio renovó a las 08:15:02 y no registró ninguna caducidad al pasar el minuto 20. La
reautenticación queda como segunda barrera.

**Seguimiento recomendado (sin cambios de código):** comprobar en el journal de las próximas 24 h
que `INVALID_SESSION_INFORMATION` desaparece o queda en casos aislados. Es el criterio de «hecho»
de 3.5-A.

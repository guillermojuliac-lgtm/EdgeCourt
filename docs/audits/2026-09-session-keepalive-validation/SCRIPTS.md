# Scripts y salidas de la validación de keepAlive (Phase 3.5-A)

Copia literal de las sondas ejecutadas el 2026-09-27 contra Betfair real, con sesiones propias, efímeras y de solo lectura (`listEvents` + identidad). Se guardan como Markdown para no entrar en el lint del proyecto. No imprimen token, App Key ni cabeceras.

## `ka_probe.py`

```
"""Prueba real de keepAlive (solo lectura). No imprime token ni App Key."""
from datetime import UTC, datetime
from edgecourt.config import get_settings
from edgecourt.market import auth
from edgecourt.market.client import MarketFilter, ReadOnlyBettingClient
s = get_settings(); sm = auth.SessionManager(s)
try:
    sess = sm.current()
    print("jurisdiccion:", sess.jurisdiction, "| intervalo keepAlive:", auth.keep_alive_interval(sess.jurisdiction))
    t0 = sess.last_keep_alive
    ok = auth.keep_alive(sess, client=sm.client)
    print("keepAlive aceptado por Betfair:", ok, "| last_keep_alive avanzado:", sess.last_keep_alive > t0)
    ev = ReadOnlyBettingClient(sm).list_events(MarketFilter())
    print("lectura tras keepAlive OK, eventos de tenis:", len(ev), "| misma sesion:", sm.current() is sess)
    print("hora UTC:", datetime.now(UTC).isoformat(timespec="seconds"))
finally:
    sm.close()
```

## `ka_extension_probe.py`

```
"""Sonda real (solo lectura): el keepAlive a los 15 min extiende la sesion .es mas alla de 20 min.
Las lecturas se hacen SIN reautenticacion automatica, para que una sesion caducada se vea.
No imprime token ni App Key."""
import time
from datetime import UTC, datetime
from edgecourt.config import get_settings
from edgecourt.market import auth
from edgecourt.market.client import BETTING_API_BASE, MarketFilter

def ts(): return datetime.now(UTC).isoformat(timespec="seconds")
s = get_settings(); sm = auth.SessionManager(s)
def raw_read(sess):
    r = sm.client.post(f"{BETTING_API_BASE}/listEvents/", json={"filter": MarketFilter().as_payload()},
                       headers=sess.headers(), timeout=30)
    try: body = r.json()
    except ValueError: return f"http {r.status_code} no-json"
    if isinstance(body, dict):
        return "ERROR " + str(body.get("detail", {}).get("APINGException", {}).get("errorCode") or body.get("faultcode"))
    return f"OK ({len(body)} eventos)"
try:
    sess = auth.login(s, client=sm.client); t0 = time.monotonic()
    print(ts(), "t=0 login OK, jurisdiccion", sess.jurisdiction, flush=True)
    print(ts(), "t=0 lectura:", raw_read(sess), flush=True)
    time.sleep(15*60)
    print(ts(), f"t={int((time.monotonic()-t0)/60)} min keepAlive aceptado:", auth.keep_alive(sess, client=sm.client), flush=True)
    time.sleep(6*60)
    print(ts(), f"t={int((time.monotonic()-t0)/60)} min lectura sin reauth:", raw_read(sess), flush=True)
    time.sleep(3*60)
    print(ts(), f"t={int((time.monotonic()-t0)/60)} min lectura sin reauth:", raw_read(sess), flush=True)
finally:
    try: auth.logout(sess, client=sm.client)
    except Exception: pass
    sm.client.close()
    print(ts(), "fin; sesion de la sonda cerrada", flush=True)
```

## Salida de `ka_probe.py` (07:57 UTC)

```
jurisdiccion: es | intervalo keepAlive: 0:15:00
keepAlive aceptado por Betfair: True | last_keep_alive avanzado: True
lectura tras keepAlive OK, eventos de tenis: 3 | misma sesion: True
hora UTC: 2026-09-27T07:57:41+00:00
```

## Salida de `ka_extension_probe.py` (07:58–08:22 UTC)

```
2026-09-27T07:58:06+00:00 t=0 login OK, jurisdiccion es
2026-09-27T07:58:06+00:00 t=0 lectura: OK (3 eventos)
2026-09-27T08:13:06+00:00 t=15 min keepAlive aceptado: True
2026-09-27T08:19:06+00:00 t=21 min lectura sin reauth: OK (6 eventos)
2026-09-27T08:22:06+00:00 t=24 min lectura sin reauth: OK (6 eventos)
2026-09-27T08:22:07+00:00 fin; sesion de la sonda cerrada
```

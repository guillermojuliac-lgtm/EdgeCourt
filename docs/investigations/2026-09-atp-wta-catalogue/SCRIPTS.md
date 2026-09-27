# Scripts de la investigación 3.5-C

Copia literal de los scripts ejecutados el 2026-09-27. Se guardan como Markdown para no entrar en el lint del proyecto. Solo usan operaciones de lectura (`list*` de la Betting API y `getDeveloperAppKeys` de la Accounts API), cierran su propia sesión y no imprimen claves, tokens ni cabeceras.

## `bf_catalogue.py`

```
"""Diagnostico de SOLO LECTURA del catalogo de tenis de Betfair (Phase 3.5-C).

Usa la configuracion y el cliente del proyecto. Solo operaciones list*.
No imprime app key, token, certificados ni cabeceras. Cierra la sesion al final.
"""

import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

from edgecourt.config import get_settings
from edgecourt.market.auth import SessionManager
from edgecourt.market.client import MarketFilter, ReadOnlyBettingClient

READ_ONLY_OPS = {
    "listEventTypes",
    "listCompetitions",
    "listEvents",
    "listMarketTypes",
    "listMarketCatalogue",
    "listMarketBook",
    "listCountries",
}

out_dir = sys.argv[1]
settings = get_settings()
sessions = SessionManager(settings)
client = ReadOnlyBettingClient(sessions)


def call(op, payload):
    assert op in READ_ONLY_OPS, op
    return client._call(op, payload) or []


def dump(name, data):
    with open(f"{out_dir}/{name}.json", "w") as fh:
        json.dump(data, fh, indent=1, default=str, ensure_ascii=False)


now = datetime.now(UTC)
report = {"queried_at": now.isoformat(), "jurisdiction": settings.betfair_jurisdiction}
try:
    # 1. Event types (todas las disciplinas visibles para la sesion)
    et = call("listEventTypes", {"filter": {}})
    dump("01_event_types", et)
    report["event_types"] = sorted(
        (e["eventType"]["name"], e["eventType"]["id"], e["marketCount"]) for e in et
    )

    tennis = {"eventTypeIds": ["2"]}

    # 2. Competiciones de tenis, sin filtro de tipo de mercado ni de tiempo
    comps = call("listCompetitions", {"filter": tennis})
    dump("02_competitions_all", comps)
    report["competitions_all"] = sorted(
        (c["competition"]["name"], c["competition"]["id"], c.get("competitionRegion"), c["marketCount"])
        for c in comps
    )

    # 3. Competiciones con MATCH_ODDS
    comps_mo = call("listCompetitions", {"filter": {**tennis, "marketTypeCodes": ["MATCH_ODDS"]}})
    dump("03_competitions_match_odds", comps_mo)
    report["competitions_match_odds"] = sorted(
        (c["competition"]["name"], c["marketCount"]) for c in comps_mo
    )

    # 4. Eventos de tenis
    events = call("listEvents", {"filter": tennis})
    dump("04_events_all", events)
    report["events_all_count"] = len(events)

    # 5. Tipos de mercado de tenis
    mtypes = call("listMarketTypes", {"filter": tennis})
    dump("05_market_types", mtypes)
    report["market_types"] = sorted((m["marketType"], m["marketCount"]) for m in mtypes)

    proj = ["COMPETITION", "EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION", "MARKET_DESCRIPTION"]

    # 6. Catalogo amplio: todos los mercados de tenis, sin ventana temporal
    broad = call(
        "listMarketCatalogue",
        {"filter": tennis, "marketProjection": proj, "maxResults": 1000, "sort": "FIRST_TO_START"},
    )
    dump("06_catalogue_broad", broad)

    # 7. MATCH_ODDS sin ventana temporal
    mo_all = call(
        "listMarketCatalogue",
        {
            "filter": {**tennis, "marketTypeCodes": ["MATCH_ODDS"]},
            "marketProjection": proj,
            "maxResults": 1000,
            "sort": "FIRST_TO_START",
        },
    )
    dump("07_catalogue_match_odds_all", mo_all)

    # 8. Consulta EXACTA de EdgeCourt (misma ventana que Collector._catalogue_window)
    horizon = now + timedelta(hours=24 + 2)
    ec_filter = MarketFilter(
        market_start_from=now.isoformat().replace("+00:00", "Z"),
        market_start_to=horizon.isoformat().replace("+00:00", "Z"),
    )
    ec = client.list_market_catalogue(ec_filter)  # maxResults=200, igual que el collector
    dump("08_catalogue_edgecourt", ec)

    # 9. MATCH_ODDS con inicio en el pasado reciente (partidos retrasados o en juego)
    past = call(
        "listMarketCatalogue",
        {
            "filter": {
                **tennis,
                "marketTypeCodes": ["MATCH_ODDS"],
                "marketStartTime": {
                    "from": (now - timedelta(hours=12)).isoformat().replace("+00:00", "Z"),
                    "to": now.isoformat().replace("+00:00", "Z"),
                },
            },
            "marketProjection": proj,
            "maxResults": 1000,
        },
    )
    dump("09_catalogue_match_odds_past12h", past)

    # 10. Libro de los MATCH_ODDS: estado, inplay y totalMatched (sin precios)
    ids = [m["marketId"] for m in mo_all]
    books = []
    for i in range(0, len(ids), 40):
        books += call("listMarketBook", {"marketIds": ids[i : i + 40]})
    dump("10_books_match_odds", books)

    def summarise(cat):
        return Counter(
            ((m.get("competition") or {}).get("name") or "(sin competicion)") for m in cat
        ).most_common()

    report["broad_count"] = len(broad)
    report["broad_by_competition"] = summarise(broad)
    report["broad_by_market_type"] = Counter(
        (m.get("description") or {}).get("marketType") for m in broad
    ).most_common()
    report["match_odds_all_count"] = len(mo_all)
    report["match_odds_by_competition"] = summarise(mo_all)
    report["edgecourt_count"] = len(ec)
    report["edgecourt_by_competition"] = summarise(ec)
    report["past12h_count"] = len(past)
    report["past12h_by_competition"] = summarise(past)
    bk = {b["marketId"]: b for b in books}
    report["match_odds_detail"] = [
        {
            "marketId": m["marketId"],
            "competition": (m.get("competition") or {}).get("name"),
            "event": (m.get("event") or {}).get("name"),
            "countryCode": (m.get("event") or {}).get("countryCode"),
            "marketStartTime": m.get("marketStartTime"),
            "runners": [r.get("runnerName") for r in m.get("runners", [])],
            "status": bk.get(m["marketId"], {}).get("status"),
            "inplay": bk.get(m["marketId"], {}).get("inplay"),
            "totalMatched": bk.get(m["marketId"], {}).get("totalMatched"),
            "in_edgecourt_window": m["marketId"] in {x["marketId"] for x in ec},
        }
        for m in mo_all
    ]
    dump("00_report", report)
finally:
    sessions.close()

print(json.dumps(report, indent=1, default=str, ensure_ascii=False))
```

## `bf_appkey.py`

```
"""Solo lectura: tipo de la Application Key configurada. No imprime ninguna clave."""
from edgecourt.config import get_settings
from edgecourt.market.auth import SessionManager
s = get_settings(); sm = SessionManager(s)
try:
    sess = sm.current()
    r = sm.client.post("https://api.betfair.com/exchange/account/rest/v1.0/getDeveloperAppKeys/",
                       json={}, headers=sess.headers(), timeout=20)
    print("http_status", r.status_code)
    body = r.json()
    if isinstance(body, dict):
        print("error", body.get("detail", {}).get("AccountAPINGException", {}).get("errorCode") or body.get("faultstring"))
    else:
        for app in body:
            for v in app.get("appVersions", []):
                print({"configured_key": v.get("applicationKey") == s.betfair_app_key,
                       "delayData": v.get("delayData"), "active": v.get("active"),
                       "subscriptionRequired": v.get("subscriptionRequired"),
                       "ownerManaged": v.get("ownerManaged")})
finally:
    sm.close()
```

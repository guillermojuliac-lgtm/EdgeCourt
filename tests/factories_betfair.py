"""Respuestas simuladas de la API de Betfair, con la forma real de la documentacion.

No se usa ninguna credencial ni ninguna llamada de red en los tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def catalogue(
    market_id: str = "1.234567890",
    *,
    start_in_minutes: float = 60.0,
    now: datetime | None = None,
    player_a: str = "Carlos Alcaraz",
    player_b: str = "Jannik Sinner",
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    start = now + timedelta(minutes=start_in_minutes)
    return {
        "marketId": market_id,
        "marketName": "Match Odds",
        "marketStartTime": start.isoformat().replace("+00:00", "Z"),
        "competition": {"id": "12345", "name": "ATP Masters"},
        "event": {
            "id": "33445566",
            "name": f"{player_a} v {player_b}",
            "countryCode": "GB",
            "timezone": "GMT",
            "openDate": start.isoformat().replace("+00:00", "Z"),
        },
        "runners": [
            {"selectionId": 1001, "runnerName": player_a, "sortPriority": 1},
            {"selectionId": 1002, "runnerName": player_b, "sortPriority": 2},
        ],
    }


def market_book(
    market_id: str = "1.234567890",
    *,
    status: str = "OPEN",
    inplay: bool = False,
    back_a: float = 1.72,
    lay_a: float = 1.74,
    back_b: float = 2.30,
    lay_b: float = 2.34,
    size: float = 500.0,
) -> dict[str, Any]:
    def runner(selection_id: int, back: float, lay: float) -> dict[str, Any]:
        return {
            "selectionId": selection_id,
            "status": "ACTIVE",
            "totalMatched": 12345.67,
            "lastPriceTraded": (back + lay) / 2,
            "ex": {
                "availableToBack": [
                    {"price": back, "size": size},
                    {"price": round(back - 0.02, 2), "size": size * 2},
                    {"price": round(back - 0.04, 2), "size": size * 3},
                ],
                "availableToLay": [
                    {"price": lay, "size": size},
                    {"price": round(lay + 0.02, 2), "size": size * 2},
                    {"price": round(lay + 0.04, 2), "size": size * 3},
                ],
            },
        }

    return {
        "marketId": market_id,
        "status": status,
        "inplay": inplay,
        "betDelay": 5 if inplay else 0,
        "numberOfActiveRunners": 2,
        "totalMatched": 98765.43,
        "runners": [runner(1001, back_a, lay_a), runner(1002, back_b, lay_b)],
    }

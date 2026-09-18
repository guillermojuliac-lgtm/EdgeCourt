"""Puente entre las respuestas de Betfair y el modelo de datos operativo.

Convierte lo que devuelve `listMarketCatalogue` y `listMarketBook` en las
estructuras que persiste `db.repositories`. Vive aparte del cliente (que solo
habla HTTP) y de los repositorios (que solo hablan SQL), porque traducir entre
ambos mundos es una responsabilidad propia y con reglas propias.

La regla principal: **una observacion se construye siempre**, tenga o no precios
el mercado. Que `runner_price` quede vacio es un resultado valido y esperado.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from edgecourt.db.repositories import PRICE_COLUMNS, ObservationPayload
from edgecourt.market.snapshots import _parse_time, _price_levels


def event_from_catalogue(catalogue: dict[str, Any]) -> dict[str, Any] | None:
    event = catalogue.get("event") or {}
    if not event.get("id"):
        return None
    competition = catalogue.get("competition") or {}
    return {
        "event_id": str(event["id"]),
        "event_name": str(event.get("name") or "(desconocido)"),
        "competition_id": str(competition["id"]) if competition.get("id") else None,
        "competition_name": competition.get("name"),
        "country_code": event.get("countryCode"),
        "timezone": event.get("timezone"),
        "open_date": _parse_time(event.get("openDate")),
    }


def market_from_catalogue(catalogue: dict[str, Any]) -> dict[str, Any] | None:
    event = catalogue.get("event") or {}
    start_time = _parse_time(catalogue.get("marketStartTime"))
    if not catalogue.get("marketId") or not event.get("id") or start_time is None:
        return None
    return {
        "market_id": str(catalogue["marketId"]),
        "event_id": str(event["id"]),
        "market_name": str(catalogue.get("marketName") or "Match Odds"),
        "market_type": "MATCH_ODDS",
        "market_start_time": start_time,
    }


def runners_from_catalogue(catalogue: dict[str, Any]) -> list[dict[str, Any]]:
    market_id = catalogue.get("marketId")
    if not market_id:
        return []
    runners = []
    for runner in catalogue.get("runners", []):
        if runner.get("selectionId") is None:
            continue
        runners.append(
            {
                "market_id": str(market_id),
                "selection_id": int(runner["selectionId"]),
                "runner_name": str(runner.get("runnerName") or "(desconocido)"),
                "sort_priority": runner.get("sortPriority"),
                "handicap": runner.get("handicap") or 0,
            }
        )
    return runners


def _runner_prices(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae los precios de cada runner en el formato que espera la BD."""
    rows: list[dict[str, Any]] = []
    for runner in book.get("runners", []):
        selection_id = runner.get("selectionId")
        if selection_id is None:
            continue

        exchange = runner.get("ex") or {}
        backs = _price_levels(exchange.get("availableToBack"))
        lays = _price_levels(exchange.get("availableToLay"))

        row: dict[str, Any] = {
            "selection_id": int(selection_id),
            "runner_status": runner.get("status") or "UNKNOWN",
            "last_price_traded": runner.get("lastPriceTraded"),
            "runner_total_matched": runner.get("totalMatched"),
        }
        for column in PRICE_COLUMNS:
            row[column] = None
        for index, (price, size) in enumerate(backs, start=1):
            row[f"back_price_{index}"] = price
            row[f"back_size_{index}"] = size
        for index, (price, size) in enumerate(lays, start=1):
            row[f"lay_price_{index}"] = price
            row[f"lay_size_{index}"] = size
        rows.append(row)
    return rows


def observation_from_book(
    book: dict[str, Any],
    catalogue: dict[str, Any],
    *,
    label: str,
    capture_key: str,
    observed_at: datetime,
    collector_run_id: uuid.UUID,
) -> ObservationPayload | None:
    """Construye la observacion a persistir.

    `minutes_to_start` se calcula con el instante **real** de observacion, no con
    el hito al que apuntaba la captura: un proceso 24/7 sufre reinicios y
    retrasos, y anotar la hora teorica convertiria esos desajustes en datos
    falsos (brief §11).
    """
    market_id = book.get("marketId") or catalogue.get("marketId")
    if not market_id:
        return None

    start_time = _parse_time(catalogue.get("marketStartTime"))
    minutes_to_start = (start_time - observed_at).total_seconds() / 60.0 if start_time else 0.0

    return ObservationPayload(
        market_id=str(market_id),
        observed_at=observed_at.astimezone(UTC),
        capture_key=capture_key,
        snapshot_label=label,
        minutes_to_start=minutes_to_start,
        market_status=str(book.get("status") or "UNKNOWN"),
        inplay=bool(book.get("inplay")),
        bet_delay=book.get("betDelay"),
        active_runners=book.get("numberOfActiveRunners"),
        total_matched=book.get("totalMatched"),
        collector_run_id=collector_run_id,
        runners=_runner_prices(book),
    )

"""Cliente de la Betting API de Betfair. **SOLO LECTURA.**

Se implementan exactamente tres operaciones, todas de consulta:

* `listEvents`          - que partidos hay;
* `listMarketCatalogue` - que mercados tiene cada partido y quienes compiten;
* `listMarketBook`      - precios back/lay, liquidez y estado del mercado.

No existe ninguna operacion de escritura, y el test
`tests/test_no_real_betting_surface.py` falla si alguien anade una.

Limites respetados (documentacion de Betfair):

* `listMarketBook` con `EX_BEST_OFFERS` tiene un peso de 5 por mercado y un
  maximo de 200 por peticion, de donde salen los 40 mercados por lote.
* Los errores `TOO_MANY_REQUESTS` se tratan con espera exponencial: la API
  limita por peso, no solo por numero de llamadas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import httpx

from edgecourt.logging_setup import get_logger
from edgecourt.market.auth import SessionManager, wait_with_backoff

log = get_logger("collector.client")

BETTING_API_BASE: Final[str] = "https://api.betfair.com/exchange/betting/rest/v1.0"

# Identificador del deporte "Tennis" en Betfair.
TENNIS_EVENT_TYPE_ID: Final[str] = "2"

# Mercado principal: ganador del partido.
MATCH_ODDS: Final[str] = "MATCH_ODDS"

# Maximo de mercados por llamada a listMarketBook con EX_BEST_OFFERS.
MARKET_BOOK_BATCH_SIZE: Final[int] = 40

MAX_ATTEMPTS: Final[int] = 5
REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0

# Errores de Betfair que justifican reintentar.
RETRYABLE_ERRORS: Final[frozenset[str]] = frozenset(
    {"TOO_MANY_REQUESTS", "SERVICE_BUSY", "TIMEOUT_ERROR", "UNEXPECTED_ERROR"}
)

# Errores que obligan a rehacer la sesion.
SESSION_ERRORS: Final[frozenset[str]] = frozenset(
    {"INVALID_SESSION_INFORMATION", "NO_SESSION", "INVALID_APP_KEY"}
)


class BetfairApiError(RuntimeError):
    """Error devuelto por la API de Betfair."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class MarketFilter:
    """Filtro de consulta. Solo lo que el collector necesita."""

    event_type_ids: tuple[str, ...] = (TENNIS_EVENT_TYPE_ID,)
    market_type_codes: tuple[str, ...] = (MATCH_ODDS,)
    market_start_from: str | None = None
    market_start_to: str | None = None
    in_play_only: bool | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "eventTypeIds": list(self.event_type_ids),
            "marketTypeCodes": list(self.market_type_codes),
        }
        if self.market_start_from or self.market_start_to:
            window: dict[str, str] = {}
            if self.market_start_from:
                window["from"] = self.market_start_from
            if self.market_start_to:
                window["to"] = self.market_start_to
            payload["marketStartTime"] = window
        if self.in_play_only is not None:
            payload["inPlayOnly"] = self.in_play_only
        return payload


def _extract_error_code(payload: Any) -> str | None:
    """Saca el codigo de error de las varias formas que usa Betfair."""
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        exception = detail.get("APINGException")
        if isinstance(exception, dict) and exception.get("errorCode"):
            return str(exception["errorCode"])
    if payload.get("faultstring"):
        return str(payload["faultstring"])
    if payload.get("errorCode"):
        return str(payload["errorCode"])
    return None


class ReadOnlyBettingClient:
    """Acceso de solo lectura a los mercados de tenis de Betfair."""

    def __init__(self, sessions: SessionManager, *, base_url: str = BETTING_API_BASE) -> None:
        self._sessions = sessions
        self._base_url = base_url.rstrip("/")

    # -- Transporte -----------------------------------------------------------

    def _call(self, operation: str, payload: dict[str, Any]) -> Any:
        """Llama a una operacion de lectura, con reintentos y backoff."""
        url = f"{self._base_url}/{operation}/"
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            session = self._sessions.current()
            try:
                response = self._sessions.client.post(
                    url, json=payload, headers=session.headers(), timeout=REQUEST_TIMEOUT_SECONDS
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    break
                delay = wait_with_backoff(attempt)
                log.warning(
                    "error de red, reintentando",
                    extra={"operation": operation, "attempt": attempt, "delay_s": delay},
                )
                continue

            try:
                body = response.json()
            except ValueError:
                body = None

            code = _extract_error_code(body)
            if code is None and response.status_code < 400:
                return body

            if code in SESSION_ERRORS:
                log.info("sesion invalida, forzando reautenticacion", extra={"code": code})
                self._sessions.invalidate()
                if attempt == MAX_ATTEMPTS:
                    raise BetfairApiError(code)
                continue

            if code in RETRYABLE_ERRORS or response.status_code >= 500:
                last_error = BetfairApiError(code or str(response.status_code))
                if attempt == MAX_ATTEMPTS:
                    break
                delay = wait_with_backoff(attempt)
                log.warning(
                    "error recuperable, reintentando",
                    extra={
                        "operation": operation,
                        "code": code,
                        "attempt": attempt,
                        "delay_s": delay,
                    },
                )
                continue

            raise BetfairApiError(code or f"HTTP {response.status_code}")

        raise BetfairApiError("MAX_ATTEMPTS_EXCEEDED", str(last_error))

    # -- Operaciones de lectura ----------------------------------------------

    def list_events(self, market_filter: MarketFilter) -> list[dict[str, Any]]:
        """Partidos que encajan con el filtro."""
        result = self._call("listEvents", {"filter": market_filter.as_payload()})
        return result or []

    def list_market_catalogue(
        self, market_filter: MarketFilter, *, max_results: int = 200
    ) -> list[dict[str, Any]]:
        """Mercados con sus participantes y hora de inicio."""
        payload = {
            "filter": market_filter.as_payload(),
            "marketProjection": [
                "EVENT",
                "COMPETITION",
                "MARKET_START_TIME",
                "RUNNER_DESCRIPTION",
            ],
            "maxResults": max_results,
            "sort": "FIRST_TO_START",
        }
        result = self._call("listMarketCatalogue", payload)
        return result or []

    def list_market_book(self, market_ids: list[str]) -> list[dict[str, Any]]:
        """Precios back/lay y liquidez de los mercados indicados.

        Se trocea en lotes para respetar el limite de peso de la API.
        """
        books: list[dict[str, Any]] = []
        for start in range(0, len(market_ids), MARKET_BOOK_BATCH_SIZE):
            batch = market_ids[start : start + MARKET_BOOK_BATCH_SIZE]
            payload = {
                "marketIds": batch,
                "priceProjection": {
                    "priceData": ["EX_BEST_OFFERS"],
                    "exBestOffersOverrides": {"bestPricesDepth": 3},
                    "virtualise": True,
                },
            }
            result = self._call("listMarketBook", payload)
            books.extend(result or [])
        return books

"""Tests del cliente de lectura: lotes, reintentos y manejo de errores."""

from __future__ import annotations

import httpx
import pytest
from tests.factories_betfair import catalogue, market_book

from edgecourt.market import auth
from edgecourt.market.client import (
    MARKET_BOOK_BATCH_SIZE,
    MATCH_ODDS,
    TENNIS_EVENT_TYPE_ID,
    BetfairApiError,
    MarketFilter,
    ReadOnlyBettingClient,
)

FAKE_TOKEN = "token-de-prueba-no-real-0123456789"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda _s: None)


class _StubSessions:
    """SessionManager simulado: evita tocar credenciales y TLS en los tests."""

    def __init__(self, handler) -> None:
        self.client = httpx.Client(transport=httpx.MockTransport(handler))
        self.invalidations = 0
        self._session = auth.Session(
            FAKE_TOKEN,
            "app-key-ficticia",
            created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            last_keep_alive=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    def current(self):
        return self._session

    def invalidate(self):
        self.invalidations += 1


def _client(handler) -> tuple[ReadOnlyBettingClient, _StubSessions]:
    sessions = _StubSessions(handler)
    return ReadOnlyBettingClient(sessions), sessions


# --- Filtro -------------------------------------------------------------------


def test_filter_targets_tennis_match_odds_by_default():
    payload = MarketFilter().as_payload()
    assert payload["eventTypeIds"] == [TENNIS_EVENT_TYPE_ID]
    assert payload["marketTypeCodes"] == [MATCH_ODDS]


def test_filter_includes_the_time_window():
    payload = MarketFilter(
        market_start_from="2026-09-18T10:00:00Z", market_start_to="2026-09-19T10:00:00Z"
    ).as_payload()
    assert payload["marketStartTime"]["from"] == "2026-09-18T10:00:00Z"
    assert payload["marketStartTime"]["to"] == "2026-09-19T10:00:00Z"


# --- Operaciones de lectura ---------------------------------------------------


def test_list_market_catalogue_sends_auth_headers():
    seen = {}

    def handler(request):
        seen["app"] = request.headers.get("X-Application")
        seen["auth"] = request.headers.get("X-Authentication")
        seen["path"] = request.url.path
        return httpx.Response(200, json=[catalogue()])

    client, _ = _client(handler)
    result = client.list_market_catalogue(MarketFilter())

    assert len(result) == 1
    assert seen["app"] == "app-key-ficticia"
    assert seen["auth"] == FAKE_TOKEN
    assert seen["path"].endswith("/listMarketCatalogue/")


def test_list_events_returns_empty_list_when_nothing_matches():
    client, _ = _client(lambda r: httpx.Response(200, json=None))
    assert client.list_events(MarketFilter()) == []


@pytest.mark.critical
def test_market_book_is_split_into_batches():
    """El limite de peso de la API obliga a trocear: no puede enviarse todo junto."""
    batches = []

    def handler(request):
        import json

        payload = json.loads(request.content)
        batches.append(len(payload["marketIds"]))
        return httpx.Response(200, json=[market_book(m) for m in payload["marketIds"]])

    client, _ = _client(handler)
    ids = [f"1.{i:09d}" for i in range(95)]
    result = client.list_market_book(ids)

    assert batches == [MARKET_BOOK_BATCH_SIZE, MARKET_BOOK_BATCH_SIZE, 15]
    assert len(result) == 95


def test_market_book_requests_three_levels_of_depth():
    seen = {}

    def handler(request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json=[])

    client, _ = _client(handler)
    client.list_market_book(["1.1"])

    projection = seen["priceProjection"]
    assert projection["priceData"] == ["EX_BEST_OFFERS"]
    assert projection["exBestOffersOverrides"]["bestPricesDepth"] == 3


def test_empty_market_list_makes_no_calls():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=[])

    client, _ = _client(handler)
    assert client.list_market_book([]) == []
    assert not calls


# --- Errores ------------------------------------------------------------------


@pytest.mark.critical
def test_rate_limit_is_retried_with_backoff():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(
                400, json={"detail": {"APINGException": {"errorCode": "TOO_MANY_REQUESTS"}}}
            )
        return httpx.Response(200, json=[catalogue()])

    client, _ = _client(handler)
    assert len(client.list_market_catalogue(MarketFilter())) == 1
    assert attempts["n"] == 3


@pytest.mark.critical
def test_invalid_session_triggers_reauthentication():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(
                400,
                json={"detail": {"APINGException": {"errorCode": "INVALID_SESSION_INFORMATION"}}},
            )
        return httpx.Response(200, json=[catalogue()])

    client, sessions = _client(handler)
    client.list_market_catalogue(MarketFilter())
    assert sessions.invalidations == 1


def test_non_retryable_error_raises_immediately():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(
            400, json={"detail": {"APINGException": {"errorCode": "INVALID_INPUT_DATA"}}}
        )

    client, _ = _client(handler)
    with pytest.raises(BetfairApiError, match="INVALID_INPUT_DATA"):
        client.list_market_catalogue(MarketFilter())
    assert attempts["n"] == 1, "un error de entrada no debe reintentarse"


def test_server_errors_are_retried_then_give_up():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(503, json={})

    client, _ = _client(handler)
    with pytest.raises(BetfairApiError, match="MAX_ATTEMPTS_EXCEEDED"):
        client.list_market_catalogue(MarketFilter())
    assert attempts["n"] == 5


def test_network_errors_are_retried():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("caida temporal")
        return httpx.Response(200, json=[catalogue()])

    client, _ = _client(handler)
    assert len(client.list_market_catalogue(MarketFilter())) == 1

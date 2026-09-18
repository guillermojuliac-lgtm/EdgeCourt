"""Tests de la politica de captura hibrida."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.factories_betfair import catalogue

from edgecourt.market.cadence import (
    CONTINUOUS_LABEL,
    DEFAULT_CADENCE,
    CadenceRule,
    MarketState,
    adaptive_capture_key,
    cadence_for,
    estimate_daily_observations,
    milestone_due,
    plan_captures,
)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _states(**kwargs) -> dict[str, MarketState]:
    return {kwargs["market_id"]: MarketState(**kwargs)}


# --- Hitos: siempre, con o sin liquidez --------------------------------------


@pytest.mark.critical
def test_milestones_are_captured_even_without_liquidity():
    """Un mercado vacio a 24h es informacion valida, no algo que saltarse.

    Es justo lo que permite medir cuando empieza a aparecer la liquidez.
    """
    cat = catalogue("1.001", now=NOW, start_in_minutes=1440)
    planned = plan_captures([cat], {}, now=NOW)

    assert len(planned) == 1
    snapshot, key = planned[0]
    assert snapshot.label == "24h"
    assert key == "24h"


@pytest.mark.parametrize(
    ("minutes_left", "expected"),
    [(1440, "24h"), (720, "12h"), (360, "6h"), (60, "1h"), (10, "10m"), (1, "close")],
)
def test_every_milestone_is_detected(minutes_left, expected):
    assert milestone_due(minutes_left, frozenset()) == expected


def test_already_captured_milestone_is_skipped():
    assert milestone_due(1440, frozenset({"24h"})) is None


@pytest.mark.critical
def test_milestone_wins_over_adaptive():
    """Si coinciden, gana el hito: es el que permite comparar entre mercados."""
    cat = catalogue("1.001", now=NOW, start_in_minutes=10)
    states = _states(market_id="1.001", has_shown_liquidity=True)

    planned = plan_captures([cat], states, now=NOW)
    assert len(planned) == 1
    assert planned[0][0].label == "10m"


# --- Cadencia adaptativa: solo con liquidez ----------------------------------


@pytest.mark.critical
def test_adaptive_capture_requires_shown_liquidity():
    """Seguir de cerca un libro vacio solo gasta cuota de API."""
    cat = catalogue("1.001", now=NOW, start_in_minutes=45)

    sin_liquidez = plan_captures([cat], {}, now=NOW)
    assert sin_liquidez == []

    con_liquidez = plan_captures(
        [cat], _states(market_id="1.001", has_shown_liquidity=True), now=NOW
    )
    assert len(con_liquidez) == 1
    assert con_liquidez[0][0].label == CONTINUOUS_LABEL


def test_prices_alone_also_activate_the_market():
    cat = catalogue("1.001", now=NOW, start_in_minutes=45)
    planned = plan_captures([cat], _states(market_id="1.001", has_shown_prices=True), now=NOW)
    assert len(planned) == 1


@pytest.mark.parametrize(
    ("minutes_left", "expected_interval"),
    [(5, 1), (10, 1), (20, 5), (30, 5), (60, 10), (90, 10), (200, 30), (360, 30)],
)
def test_cadence_tightens_near_the_start(minutes_left, expected_interval):
    assert cadence_for(minutes_left) == expected_interval


def test_no_adaptive_cadence_far_from_the_start():
    """Mas alla de 6 horas solo hay hitos: el precio no se mueve."""
    assert cadence_for(400) is None
    assert cadence_for(1400) is None


def test_adaptive_respects_the_interval():
    """No se vuelve a observar antes de que pase el intervalo."""
    cat = catalogue("1.001", now=NOW, start_in_minutes=45)  # tramo de 10 minutos
    recent = _states(
        market_id="1.001",
        has_shown_liquidity=True,
        last_observed_at=NOW - timedelta(minutes=3),
    )
    assert plan_captures([cat], recent, now=NOW) == []

    old = _states(
        market_id="1.001",
        has_shown_liquidity=True,
        last_observed_at=NOW - timedelta(minutes=11),
    )
    assert len(plan_captures([cat], old, now=NOW)) == 1


def test_adaptive_can_be_disabled():
    cat = catalogue("1.001", now=NOW, start_in_minutes=45)
    states = _states(market_id="1.001", has_shown_liquidity=True)
    assert plan_captures([cat], states, now=NOW, adaptive_enabled=False) == []


# --- Idempotencia de la clave adaptativa -------------------------------------


@pytest.mark.critical
def test_adaptive_key_is_aligned_to_the_grid():
    """Dos ciclos dentro del mismo tramo producen la misma clave.

    Es lo que hace la escritura idempotente: la segunda actualiza en lugar de
    duplicar.
    """
    first = adaptive_capture_key(datetime(2026, 9, 18, 12, 3, 10, tzinfo=UTC), 5)
    second = adaptive_capture_key(datetime(2026, 9, 18, 12, 4, 55, tzinfo=UTC), 5)
    assert first == second

    next_bucket = adaptive_capture_key(datetime(2026, 9, 18, 12, 6, 0, tzinfo=UTC), 5)
    assert next_bucket != first


def test_adaptive_keys_are_distinguishable_from_milestones():
    key = adaptive_capture_key(NOW, 5)
    assert key.startswith("a:")
    assert key not in {"24h", "12h", "6h", "1h", "10m", "close"}


# --- Casos limite -------------------------------------------------------------


def test_started_markets_are_ignored():
    cat = catalogue("1.001", now=NOW, start_in_minutes=-5)
    assert plan_captures([cat], {}, now=NOW) == []


def test_market_without_start_time_is_ignored():
    cat = catalogue("1.001", now=NOW)
    del cat["marketStartTime"]
    assert plan_captures([cat], {}, now=NOW) == []


def test_one_capture_per_market_per_cycle():
    cat = catalogue("1.001", now=NOW, start_in_minutes=5)
    states = _states(market_id="1.001", has_shown_liquidity=True)
    assert len(plan_captures([cat], states, now=NOW)) == 1


# --- Volumen ------------------------------------------------------------------


@pytest.mark.critical
def test_volume_stays_far_below_minute_by_minute_capture():
    """La politica debe costar dos ordenes de magnitud menos que capturar cada minuto."""
    estimate = estimate_daily_observations(markets_per_day=50)
    per_market = estimate["observations_per_market_max"]

    assert per_market < 50, f"{per_market} observaciones por mercado es demasiado"
    # Capturar cada minuto durante 24h serian 1.440.
    assert per_market < 1440 / 20


def test_custom_rules_are_honoured():
    rules = (CadenceRule(within_minutes=60, every_minutes=15),)
    assert cadence_for(30, rules) == 15
    assert cadence_for(90, rules) is None


def test_default_cadence_is_monotonic():
    """Cuanto mas cerca del inicio, mas frecuente. Nunca al reves."""
    ordered = sorted(DEFAULT_CADENCE, key=lambda r: r.within_minutes)
    intervals = [r.every_minutes for r in ordered]
    assert intervals == sorted(intervals)

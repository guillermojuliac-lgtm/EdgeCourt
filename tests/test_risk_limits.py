"""Stake plano y limites de riesgo (Carril A, A4): nunca negativo, nunca por encima de un tope."""

from __future__ import annotations

import numpy as np
import pytest

from edgecourt.config import Settings
from edgecourt.risk import limits as risk
from edgecourt.risk.limits import RiskLimits, size_flat_stake

LIMITS = RiskLimits(
    bankroll=1000.0,
    max_stake_fraction=0.01,
    max_market_exposure=0.02,
    max_daily_exposure=0.05,
    max_drawdown=0.20,
)


def _size(**overrides):
    params = {
        "target_fraction": 0.01,
        "limits": LIMITS,
        "visible_size": 250.0,
        "min_stake": 2.0,
    }
    params.update(overrides)
    return size_flat_stake(**params)


def test_flat_stake_is_one_percent_of_the_initial_bankroll():
    decision = _size()

    assert decision.stake == 10.0 and decision.bets and decision.reason is None


def test_stake_is_limited_to_what_is_visible_at_the_best_back():
    assert _size(visible_size=4.0).stake == 4.0


def test_visible_size_below_the_minimum_means_no_bet():
    decision = _size(visible_size=1.5)

    assert (decision.stake, decision.reason) == (0.0, risk.VISIBLE_SIZE)


def test_daily_exposure_reduces_the_stake_then_blocks_it():
    assert _size(day_exposure=45.0).stake == 5.0

    blocked = _size(day_exposure=49.0)
    assert (blocked.stake, blocked.reason) == (0.0, risk.DAILY_LIMIT)


def test_market_exposure_blocks_the_stake():
    blocked = _size(market_exposure=19.0)

    assert (blocked.stake, blocked.reason) == (0.0, risk.MARKET_LIMIT)


def test_drawdown_at_the_limit_stops_everything():
    assert _size(drawdown=0.19).stake == 10.0

    halted = _size(drawdown=0.20)
    assert (halted.stake, halted.reason) == (0.0, risk.DRAWDOWN_LIMIT)


def test_a_larger_target_is_capped_by_the_configured_stake_limit():
    assert _size(target_fraction=0.5).stake == 10.0


def test_a_target_below_the_account_minimum_means_no_bet():
    decision = _size(min_stake=15.0)

    assert (decision.stake, decision.reason) == (0.0, risk.BELOW_MINIMUM)


def test_stake_is_floored_to_the_cent():
    assert _size(visible_size=7.999).stake == 7.99


@pytest.mark.critical
def test_stake_never_exceeds_any_cap_and_is_never_negative():
    rng = np.random.default_rng(11)
    for _ in range(2000):
        kwargs = {
            "target_fraction": float(rng.uniform(0.0, 0.2)),
            "visible_size": float(rng.uniform(0.0, 400.0)),
            "min_stake": float(rng.uniform(0.0, 12.0)),
            "market_exposure": float(rng.uniform(0.0, 30.0)),
            "day_exposure": float(rng.uniform(0.0, 80.0)),
            "drawdown": float(rng.uniform(0.0, 0.3)),
        }
        decision = _size(**kwargs)

        assert decision == _size(**kwargs)  # determinista
        assert decision.stake >= 0.0
        if decision.bets:
            assert decision.stake >= kwargs["min_stake"]
            assert decision.stake <= LIMITS.bankroll * LIMITS.max_stake_fraction + 1e-9
            assert decision.stake <= LIMITS.bankroll * kwargs["target_fraction"] + 1e-9
            assert decision.stake <= kwargs["visible_size"] + 1e-9
            assert decision.stake + kwargs["market_exposure"] <= 20.0 + 1e-9
            assert decision.stake + kwargs["day_exposure"] <= 50.0 + 1e-9
            assert kwargs["drawdown"] < LIMITS.max_drawdown
        else:
            assert decision.reason is not None


def test_limits_come_from_the_settings():
    settings = Settings(_env_file=None, bankroll=2000.0, maximum_drawdown=0.1)
    limits = RiskLimits.from_settings(settings)

    assert limits.bankroll == 2000.0 and limits.max_drawdown == 0.1
    assert limits.max_market_exposure == settings.max_market_exposure

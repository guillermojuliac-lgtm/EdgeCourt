"""Desvigado (Shin) y escalera de precios de Betfair (Carril A, A4)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from edgecourt.value import devig, ticks


def _classic_shin(price_a: float, price_b: float) -> tuple[float, float]:
    """Shin con la formula clasica (no la forma estable): referencia independiente."""
    pis = [1.0 / price_a, 1.0 / price_b]
    total = sum(pis)

    def probabilities(z: float) -> list[float]:
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / total) - z) / (2 * (1 - z)) for x in pis]

    low, high = 0.0, 1.0 - 1e-9
    for _ in range(200):
        middle = (low + high) / 2
        if sum(probabilities(middle)) > 1.0:
            low = middle
        else:
            high = middle
    a, b = probabilities((low + high) / 2)
    return a, b


PRICE_PAIRS = [
    (1.95, 1.95),
    (1.30, 3.60),
    (1.05, 12.0),
    (1.80, 2.15),
    (1.01, 40.0),
    (2.50, 1.60),
    (3.05, 1.45),
]


@pytest.mark.critical
@pytest.mark.parametrize(("price_a", "price_b"), PRICE_PAIRS)
def test_shin_probabilities_sum_to_one_and_are_valid(price_a, price_b):
    p_a, p_b = devig.shin_two_way(price_a, price_b)

    assert p_a + p_b == pytest.approx(1.0, abs=1e-12)
    assert 0.0 < p_a < 1.0 and 0.0 < p_b < 1.0


@pytest.mark.parametrize(("price_a", "price_b"), PRICE_PAIRS)
def test_shin_matches_the_classic_formula(price_a, price_b):
    stable = devig.shin_two_way(price_a, price_b)
    classic = _classic_shin(price_a, price_b)

    assert stable == pytest.approx(classic, abs=1e-7)


@pytest.mark.parametrize(("price_a", "price_b"), PRICE_PAIRS)
def test_shin_is_symmetric_in_the_order_of_the_runners(price_a, price_b):
    p_a, p_b = devig.shin_two_way(price_a, price_b)
    q_b, q_a = devig.shin_two_way(price_b, price_a)

    assert (p_a, p_b) == pytest.approx((q_a, q_b), abs=1e-12)


def test_balanced_book_is_split_evenly():
    assert devig.shin_two_way(1.95, 1.95) == pytest.approx((0.5, 0.5), abs=1e-12)


@pytest.mark.parametrize(("price_a", "price_b"), [(1.30, 3.60), (1.05, 12.0), (1.01, 40.0)])
def test_shin_gives_the_favourite_more_than_the_proportional_method(price_a, price_b):
    shin_favourite = devig.shin_two_way(price_a, price_b)[0]
    proportional_favourite = devig.proportional_two_way(price_a, price_b)[0]

    assert shin_favourite > proportional_favourite


def test_the_favourite_keeps_being_the_favourite():
    p_a, p_b = devig.shin_two_way(1.40, 3.00)

    assert p_a > 0.5 > p_b


def test_shin_is_deterministic():
    assert devig.shin_two_way(1.80, 2.15) == devig.shin_two_way(1.80, 2.15)


def test_random_books_with_margin_always_sum_to_one():
    rng = np.random.default_rng(7)
    for _ in range(500):
        favourite_probability = rng.uniform(0.02, 0.98)
        margin = rng.uniform(1.001, 1.12)
        price_a = 1.0 / (favourite_probability * margin)
        price_b = 1.0 / ((1.0 - favourite_probability) * margin)
        if min(price_a, price_b) < 1.01:
            continue
        p_a, p_b = devig.shin_two_way(price_a, price_b)
        assert p_a + p_b == pytest.approx(1.0, abs=1e-12)
        assert 0.0 < p_a < 1.0


@pytest.mark.parametrize(
    ("price_a", "price_b"),
    [(1.0, 2.0), (0.5, 2.0), (float("nan"), 2.0), (2.0, float("inf")), (-3.0, 2.0)],
)
def test_invalid_prices_are_rejected(price_a, price_b):
    with pytest.raises(ValueError, match="precio decimal invalido"):
        devig.shin_two_way(price_a, price_b)


@pytest.mark.parametrize(("price_a", "price_b"), [(2.0, 2.0), (1.90, 2.20)])
def test_a_book_without_overround_cannot_be_devigged(price_a, price_b):
    with pytest.raises(ValueError, match="sobre-redondo"):
        devig.shin_two_way(price_a, price_b)
    with pytest.raises(ValueError, match="sobre-redondo"):
        devig.proportional_two_way(price_a, price_b)


def test_overround_is_the_sum_of_implied_probabilities():
    assert devig.overround(2.0, 4.0) == pytest.approx(0.75)


# --- Escalera de precios ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (1.02, 1.01),
        (1.01, 1.01),  # suelo de la escalera
        (1.50, 1.49),
        (2.00, 1.99),  # el tramo hasta 2.00 incluido sube de 0.01 en 0.01
        (2.02, 2.00),
        (3.00, 2.98),
        (3.05, 3.00),
        (4.00, 3.95),
        (4.10, 4.00),
        (6.00, 5.90),
        (6.20, 6.00),
        (10.0, 9.8),
        (10.5, 10.0),
        (20.0, 19.5),
        (21.0, 20.0),
        (100.0, 95.0),
        (110.0, 100.0),
        (1000.0, 990.0),
    ],
)
def test_worse_back_price_is_the_next_tick_down(price, expected):
    assert ticks.worse_back_price(price) == pytest.approx(expected)


@pytest.mark.parametrize("price", [1.02, 1.5, 2.02, 3.5, 7.0, 12.0, 25.0, 40.0, 75.0, 400.0])
def test_worse_back_price_is_never_better_than_the_seen_price(price):
    assert ticks.worse_back_price(price) < price


@pytest.mark.parametrize("price", [1.0, 0.99, float("nan"), float("inf")])
def test_prices_outside_the_ladder_are_rejected(price):
    with pytest.raises(ValueError, match="escalera"):
        ticks.worse_back_price(price)

"""Probabilidad de mercado sin margen («desvigado») para mercados de dos corredores.

Funciones puras. La estrategia v1 usa **Shin sobre los mejores precios BACK**
(docs/phases/PHASE_A_STRATEGY_V1_PROPOSAL.md, §2.1): en el cierre fiable de 225 mercados
reproduce el punto medio BACK/LAY mejor que el desvigado proporcional, y solo necesita precios BACK.

Para dos resultados con probabilidades implicitas pi_i = 1 / precio_i y suma Pi > 1, Shin (1993)
reparte el sobre-redondo con una fraccion de «informados» z en [0, 1):

    p_i(z) = (sqrt(z^2 + 4 (1 - z) pi_i^2 / Pi) - z) / (2 (1 - z))

y se toma la z que hace p_1 + p_2 = 1. Aqui se usa la forma algebraicamente equivalente

    p_i(z) = 2 pi_i^2 / (Pi (sqrt(z^2 + 4 (1 - z) pi_i^2 / Pi) + z))

que no divide por (1 - z) y es estable cuando z se acerca a 1. La suma es decreciente en z,
vale sqrt(Pi) > 1 en z = 0 y (pi_1^2 + pi_2^2) / Pi < 1 en z = 1, asi que la biseccion
siempre encuentra la raiz. Numero de iteraciones fijo: el resultado es determinista.
"""

from __future__ import annotations

import math

_BISECTION_STEPS = 200
_MIN_OVERROUND = 1e-12  # por debajo no hay margen que repartir


def implied_probability(price: float) -> float:
    """Probabilidad implicita de un precio decimal (> 1)."""
    if not math.isfinite(price) or price <= 1.0:
        raise ValueError(f"precio decimal invalido: {price!r}")
    return 1.0 / price


def overround(price_a: float, price_b: float) -> float:
    """Suma de las probabilidades implicitas de los dos corredores."""
    return implied_probability(price_a) + implied_probability(price_b)


def _require_margin(price_a: float, price_b: float) -> tuple[float, float, float]:
    pi_a, pi_b = implied_probability(price_a), implied_probability(price_b)
    total = pi_a + pi_b
    if total <= 1.0 + _MIN_OVERROUND:
        raise ValueError(
            f"el libro no tiene sobre-redondo que repartir (suma {total:.6f}): "
            "precios cruzados o desfasados"
        )
    return pi_a, pi_b, total


def proportional_two_way(price_a: float, price_b: float) -> tuple[float, float]:
    """Desvigado proporcional: cada probabilidad implicita dividida por la suma."""
    pi_a, pi_b, total = _require_margin(price_a, price_b)
    return pi_a / total, pi_b / total


def shin_two_way(price_a: float, price_b: float) -> tuple[float, float]:
    """Desvigado de Shin para dos resultados. Devuelve (p_a, p_b) con p_a + p_b = 1."""
    pi_a, pi_b, total = _require_margin(price_a, price_b)

    def probabilities(z: float) -> tuple[float, float]:
        return tuple(  # type: ignore[return-value]
            2.0 * pi * pi / (total * (math.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / total) + z))
            for pi in (pi_a, pi_b)
        )

    low, high = 0.0, 1.0
    for _ in range(_BISECTION_STEPS):
        middle = (low + high) / 2.0
        if sum(probabilities(middle)) > 1.0:
            low = middle
        else:
            high = middle

    p_a, p_b = probabilities((low + high) / 2.0)
    norm = p_a + p_b  # elimina el ultimo residuo numerico: la suma es exactamente 1
    return p_a / norm, p_b / norm

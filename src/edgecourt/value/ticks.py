"""Escalera de precios (ticks) del Exchange de Betfair. Funciones puras.

Se usa solo para la variante pesimista de la estrategia v1
(docs/phases/PHASE_A_STRATEGY_V1_PROPOSAL.md, §2.5): suponer que la apuesta BACK se casa un
tick peor que el precio visto.
"""

from __future__ import annotations

import math

MIN_PRICE = 1.01

# (limite superior incluido, incremento) de cada tramo; el tramo se aplica a precios por encima
# del limite del tramo anterior.
_LADDER: tuple[tuple[float, float], ...] = (
    (2.0, 0.01),
    (3.0, 0.02),
    (4.0, 0.05),
    (6.0, 0.1),
    (10.0, 0.2),
    (20.0, 0.5),
    (30.0, 1.0),
    (50.0, 2.0),
    (100.0, 5.0),
    (1000.0, 10.0),
)


def tick_below(price: float) -> float:
    """Incremento que separa `price` del precio inmediatamente inferior."""
    for upper, increment in _LADDER:
        if price <= upper:
            return increment
    return _LADDER[-1][1]


def worse_back_price(price: float) -> float:
    """Un tick peor para quien apuesta a favor (BACK): el precio inmediatamente inferior."""
    if not math.isfinite(price) or price < MIN_PRICE:
        raise ValueError(f"precio fuera de la escalera de Betfair: {price!r}")
    return max(MIN_PRICE, round(price - tick_below(price), 2))

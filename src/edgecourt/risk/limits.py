"""Tamano de la apuesta y limites de riesgo del Carril A. Funciones puras.

Stake **plano** sobre el bankroll INICIAL (estrategia v1, §2.5): Kelly necesita probabilidades
calibradas y estables, que todavia no hay. Sobre ese stake objetivo se aplican, en este orden de
informacion, todos los topes; el resultado es el MENOR de ellos, redondeado hacia abajo al centimo,
y si queda por debajo del minimo de apuesta no se apuesta. Una apuesta BACK arriesga su stake, asi
que la exposicion de un mercado o de un dia es la suma de sus stakes.

Garantias (tests de propiedades): el stake nunca es negativo, nunca supera ningun tope y es
determinista.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from edgecourt.config import Settings

DRAWDOWN_LIMIT = "drawdown_maximo"
STAKE_CAP = "tope_de_stake"
MARKET_LIMIT = "limite_de_mercado"
DAILY_LIMIT = "limite_diario"
VISIBLE_SIZE = "liquidez_visible_insuficiente"
BELOW_MINIMUM = "stake_bajo_minimo"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Limites de `config.py`, expresados sobre el bankroll inicial."""

    bankroll: float
    max_stake_fraction: float
    max_market_exposure: float
    max_daily_exposure: float
    max_drawdown: float

    @classmethod
    def from_settings(cls, settings: Settings) -> RiskLimits:
        return cls(
            bankroll=settings.bankroll,
            max_stake_fraction=settings.max_stake_percentage,
            max_market_exposure=settings.max_market_exposure,
            max_daily_exposure=settings.max_daily_exposure,
            max_drawdown=settings.maximum_drawdown,
        )


@dataclass(frozen=True, slots=True)
class StakeDecision:
    """`stake` es 0.0 cuando no se apuesta; `reason` dice que tope lo impidio."""

    stake: float
    reason: str | None = None

    @property
    def bets(self) -> bool:
        return self.stake > 0.0


def _floor_cents(amount: float) -> float:
    return math.floor(amount * 100.0 + 1e-9) / 100.0


def size_flat_stake(
    *,
    target_fraction: float,
    limits: RiskLimits,
    visible_size: float,
    min_stake: float,
    market_exposure: float = 0.0,
    day_exposure: float = 0.0,
    drawdown: float = 0.0,
) -> StakeDecision:
    """Stake plano limitado por todos los topes.

    Args:
        target_fraction: fraccion del bankroll inicial que la estrategia quiere arriesgar.
        visible_size: tamano disponible al mejor BACK del corredor.
        min_stake: apuesta minima de la cuenta; por debajo no se apuesta.
        market_exposure: exposicion ya abierta en este mercado.
        day_exposure: exposicion ya abierta ese dia.
        drawdown: caida actual desde el maximo del bankroll (0,05 = 5 %).
    """
    if drawdown >= limits.max_drawdown:
        return StakeDecision(0.0, DRAWDOWN_LIMIT)

    caps: tuple[tuple[str, float], ...] = (
        (STAKE_CAP, limits.bankroll * min(target_fraction, limits.max_stake_fraction)),
        (MARKET_LIMIT, limits.bankroll * limits.max_market_exposure - market_exposure),
        (DAILY_LIMIT, limits.bankroll * limits.max_daily_exposure - day_exposure),
        (VISIBLE_SIZE, visible_size),
    )
    binding, smallest = min(caps, key=lambda cap: cap[1])
    stake = max(0.0, _floor_cents(smallest))
    if stake <= 0.0 or stake < min_stake:
        # Si el tope mas estrecho es el propio objetivo, el minimo de la cuenta es el motivo.
        return StakeDecision(0.0, BELOW_MINIMUM if binding == STAKE_CAP else binding)
    return StakeDecision(stake)

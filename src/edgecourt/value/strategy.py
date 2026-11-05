"""Estrategia v1 del Carril A: edge, EV despues de comision y decision por mercado.

Funciones puras. La estrategia esta CONGELADA (docs/phases/PHASE_A_STRATEGY_V1_PROPOSAL.md,
SHA-256 en `STRATEGY_V1_SHA256`): sus umbrales no se ajustan con resultados paper; un cambio
es la version v2 (DEC-022). Solo dos valores son **supuestos corregibles**, no estrategia: la
comision y el stake minimo de la cuenta, ambos sin verificar contra la cuenta `.es`.

Regla (§1 del documento):

* probabilidad de mercado = Shin sobre los mejores precios BACK de los dos corredores;
* `edge = p_modelo - p_mercado`; `EV neto = p (precio - 1) (1 - comision) - (1 - p)`;
* de los dos corredores se mira el de mayor EV neto (como mucho una apuesta por mercado);
* se apuesta si `edge >= 3 pp` y `EV neto >= +3 %`; si `edge > 15 pp` NO se apuesta y se
  registra como anomalia (un edge enorme es mucho mas probable que sea un error de identidad o
  de datos que una ventaja real);
* BACK solo, stake plano limitado por `risk.limits` y por el tamano visible al mejor BACK.

El modelo nunca ve precios: el precio entra aqui, despues de la prediccion.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime

from edgecourt.config import Settings
from edgecourt.risk.limits import RiskLimits, size_flat_stake
from edgecourt.value.devig import shin_two_way
from edgecourt.value.ticks import MIN_PRICE, worse_back_price

STRATEGY_VERSION = "v1"
STRATEGY_V1_SHA256 = "32fa7eda9958d76b9a77335aa80ba407a0a78f40ec547ec337ee8306d07b6b63"

CANDIDATE = "candidato"
ANOMALY = "anomalia"
EXCLUDED = "excluido"

NO_PREDICTION = "sin_prediccion"
INCONSISTENT_PREDICTION = "prediccion_incoherente"
NO_BACK_PRICES = "sin_precios_back"
LOW_MARKET_LIQUIDITY = "liquidez_de_mercado_baja"
INCOHERENT_BOOK = "libro_incoherente"
BELOW_EDGE = "edge_insuficiente"
BELOW_EV = "ev_insuficiente"
ANOMALOUS_EDGE = "edge_anomalo"

# Las dos probabilidades del modelo para un mismo mercado deben sumar 1 (A3 las guarda asi).
_PREDICTION_SUM_TOLERANCE = 1e-3


@dataclass(frozen=True, slots=True)
class StrategyV1:
    """Parametros de la estrategia v1. Los umbrales estan congelados."""

    min_edge: float = 0.03
    min_ev_after_costs: float = 0.03
    max_edge: float = 0.15
    stake_fraction: float = 0.01
    window_min_minutes: float = 20.0
    window_max_minutes: float = 60.0
    # --- Supuestos corregibles (no son estrategia) ---
    commission: float = 0.05  # SIN VERIFICAR contra la cuenta .es
    min_stake: float = 2.0  # SUPUESTO SIN VERIFICAR: minimo de apuesta de la cuenta
    min_market_liquidity: float = 50.0  # MINIMUM_LIQUIDITY, DEC-012 (sin cambios)

    @classmethod
    def from_settings(cls, settings: Settings) -> StrategyV1:
        """Los umbrales no se leen de la configuracion: solo la comision y la liquidez."""
        return cls(
            commission=settings.betfair_commission,
            min_market_liquidity=settings.minimum_liquidity,
        )


@dataclass(frozen=True, slots=True)
class RunnerBook:
    """Un corredor en la observacion de decision."""

    selection_id: int
    name: str
    back_price: float | None  # mejor precio BACK
    back_size: float | None  # tamano disponible a ese precio
    model_probability: float | None


@dataclass(frozen=True, slots=True)
class MarketBook:
    """Un mercado de dos corredores en la observacion de decision."""

    market_id: str
    observation_id: int
    observed_at: datetime
    minutes_to_start: float
    total_available: float
    runners: tuple[RunnerBook, RunnerBook]


@dataclass(frozen=True, slots=True)
class RunnerEvaluation:
    selection_id: int
    name: str
    price: float
    model_probability: float
    market_probability_raw: float  # 1 / precio, con margen
    market_probability_devig: float  # Shin
    edge: float
    expected_value: float  # bruto
    expected_value_after_costs: float
    expected_value_after_costs_pessimistic: float  # a un tick peor
    visible_size: float


@dataclass(frozen=True, slots=True)
class Evaluation:
    market_id: str
    observation_id: int
    observed_at: datetime
    minutes_to_start: float
    title: str
    status: str
    reason: str | None = None
    runners: tuple[RunnerEvaluation, ...] = ()
    chosen: RunnerEvaluation | None = None
    stake: float = 0.0


def expected_value(probability: float, price: float) -> float:
    """EV por unidad apostada, sin comision: p (precio - 1) - (1 - p)."""
    return probability * price - 1.0


def expected_value_after_costs(probability: float, price: float, commission: float) -> float:
    """EV por unidad apostada tras la comision sobre ganancias: p (precio - 1) (1 - c) - (1 - p)."""
    return probability * (price - 1.0) * (1.0 - commission) - (1.0 - probability)


def _title(book: MarketBook) -> str:
    return f"{book.runners[0].name} v {book.runners[1].name}"


def _result(book: MarketBook, status: str, reason: str | None, **extra) -> Evaluation:
    return Evaluation(
        market_id=book.market_id,
        observation_id=book.observation_id,
        observed_at=book.observed_at,
        minutes_to_start=book.minutes_to_start,
        title=_title(book),
        status=status,
        reason=reason,
        **extra,
    )


def evaluate_market(book: MarketBook, strategy: StrategyV1) -> Evaluation:
    """Aplica la regla v1 a un mercado. No dimensiona el stake (ver `decide_market`)."""
    first, second = book.runners

    probabilities = (first.model_probability, second.model_probability)
    if any(p is None or not 0.0 < p < 1.0 for p in probabilities):
        return _result(book, EXCLUDED, NO_PREDICTION)
    if abs(probabilities[0] + probabilities[1] - 1.0) > _PREDICTION_SUM_TOLERANCE:
        return _result(book, EXCLUDED, INCONSISTENT_PREDICTION)

    if any(
        r.back_price is None or r.back_price < MIN_PRICE or r.back_size is None or r.back_size <= 0
        for r in book.runners
    ):
        return _result(book, EXCLUDED, NO_BACK_PRICES)
    if book.total_available < strategy.min_market_liquidity:
        return _result(book, EXCLUDED, LOW_MARKET_LIQUIDITY)

    try:
        devig = shin_two_way(first.back_price, second.back_price)  # type: ignore[arg-type]
    except ValueError:
        return _result(book, EXCLUDED, INCOHERENT_BOOK)

    evaluations = tuple(
        RunnerEvaluation(
            selection_id=runner.selection_id,
            name=runner.name,
            price=runner.back_price,  # type: ignore[arg-type]
            model_probability=runner.model_probability,  # type: ignore[arg-type]
            market_probability_raw=1.0 / runner.back_price,  # type: ignore[operator]
            market_probability_devig=p_market,
            edge=runner.model_probability - p_market,  # type: ignore[operator]
            expected_value=expected_value(runner.model_probability, runner.back_price),  # type: ignore[arg-type]
            expected_value_after_costs=expected_value_after_costs(
                runner.model_probability,  # type: ignore[arg-type]
                runner.back_price,  # type: ignore[arg-type]
                strategy.commission,
            ),
            expected_value_after_costs_pessimistic=expected_value_after_costs(
                runner.model_probability,  # type: ignore[arg-type]
                worse_back_price(runner.back_price),  # type: ignore[arg-type]
                strategy.commission,
            ),
            visible_size=runner.back_size,  # type: ignore[arg-type]
        )
        for runner, p_market in zip(book.runners, devig, strict=True)
    )

    chosen = max(evaluations, key=lambda e: (e.expected_value_after_costs, -e.selection_id))
    if chosen.edge > strategy.max_edge:
        return _result(book, ANOMALY, ANOMALOUS_EDGE, runners=evaluations, chosen=chosen)
    if chosen.edge < strategy.min_edge:
        return _result(book, EXCLUDED, BELOW_EDGE, runners=evaluations, chosen=chosen)
    if chosen.expected_value_after_costs < strategy.min_ev_after_costs:
        return _result(book, EXCLUDED, BELOW_EV, runners=evaluations, chosen=chosen)
    return _result(book, CANDIDATE, None, runners=evaluations, chosen=chosen)


def decide_market(
    book: MarketBook,
    strategy: StrategyV1,
    limits: RiskLimits,
    *,
    market_exposure: float = 0.0,
    day_exposure: float = 0.0,
    drawdown: float = 0.0,
) -> Evaluation:
    """Regla v1 mas dimensionado del stake con todos los limites de riesgo."""
    evaluation = evaluate_market(book, strategy)
    if evaluation.status != CANDIDATE or evaluation.chosen is None:
        return evaluation

    decision = size_flat_stake(
        target_fraction=strategy.stake_fraction,
        limits=limits,
        visible_size=evaluation.chosen.visible_size,
        min_stake=strategy.min_stake,
        market_exposure=market_exposure,
        day_exposure=day_exposure,
        drawdown=drawdown,
    )
    if not decision.bets:
        return dataclasses.replace(evaluation, status=EXCLUDED, reason=decision.reason)
    return dataclasses.replace(evaluation, stake=decision.stake)

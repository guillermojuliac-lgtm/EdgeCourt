"""Candidatos de la estrategia v1 sobre mercados proximos (Carril A, A4). Puro, solo lectura.

Recibe una fila por corredor (ver `repositories.candidate_rows`), las agrupa en mercados de dos
corredores, aplica la regla v1 en orden cronologico y acumula la exposicion diaria, de modo que el
limite diario del 5 % se respeta tambien dentro de una misma pasada. NO escribe nada: el ledger
paper es el paso A5.

Una sola decision por mercado: la observacion de decision es la primera con precios entre 20 y
60 minutos antes del inicio publicado (la elige la consulta). Si todavia no existe, el mercado
queda en `ventana_pendiente`, no se descarta.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from edgecourt.risk.limits import RiskLimits
from edgecourt.value import strategy as rules
from edgecourt.value.strategy import Evaluation, MarketBook, RunnerBook, StrategyV1

WINDOW_PENDING = "ventana_pendiente"
UNEXPECTED_SHAPE = "runners_inesperados"


@dataclass(slots=True)
class CandidatesReport:
    strategy: StrategyV1
    markets_seen: int
    candidates: list[Evaluation] = field(default_factory=list)
    anomalies: list[Evaluation] = field(default_factory=list)
    excluded: Counter = field(default_factory=Counter)

    @property
    def total_stake(self) -> float:
        return sum(e.stake for e in self.candidates)


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _utc_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def _book(market_rows: list[dict[str, Any]]) -> MarketBook:
    first = market_rows[0]
    first_runner, second_runner = (
        RunnerBook(
            selection_id=row["selection_id"],
            name=row["runner_name"],
            back_price=_number(row["back_price"]),
            back_size=_number(row["back_size"]),
            model_probability=_number(row["probability"]),
        )
        for row in market_rows
    )
    return MarketBook(
        market_id=first["market_id"],
        observation_id=first["observation_id"],
        observed_at=first["observed_at"],
        minutes_to_start=float(first["minutes_to_start"]),
        total_available=float(first["total_available"] or 0.0),
        runners=(first_runner, second_runner),
    )


def find_candidates(
    rows: list[dict[str, Any]],
    *,
    strategy: StrategyV1,
    limits: RiskLimits,
    exposure_by_day: dict[date, float] | None = None,
    drawdown: float = 0.0,
) -> CandidatesReport:
    """Evalua los mercados de `rows` con la estrategia v1.

    Args:
        exposure_by_day: exposicion ya abierta por dia UTC (vacia mientras no exista el ledger).
        drawdown: caida actual del bankroll (0 mientras no haya apuestas liquidadas).
    """
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: (r["market_id"], r["selection_id"])):
        by_market[row["market_id"]].append(row)

    report = CandidatesReport(strategy=strategy, markets_seen=len(by_market))
    books: list[MarketBook] = []
    for market_rows in by_market.values():
        if len(market_rows) != 2:
            report.excluded[UNEXPECTED_SHAPE] += 1
        elif market_rows[0]["observation_id"] is None:
            report.excluded[WINDOW_PENDING] += 1
        else:
            books.append(_book(market_rows))

    day_exposure = dict(exposure_by_day or {})
    for book in sorted(books, key=lambda b: (b.observed_at, b.market_id)):
        day = _utc_day(book.observed_at)
        evaluation = rules.decide_market(
            book,
            strategy,
            limits,
            day_exposure=day_exposure.get(day, 0.0),
            drawdown=drawdown,
        )
        if evaluation.status == rules.CANDIDATE:
            report.candidates.append(evaluation)
            day_exposure[day] = day_exposure.get(day, 0.0) + evaluation.stake
        elif evaluation.status == rules.ANOMALY:
            report.anomalies.append(evaluation)
        else:
            report.excluded[evaluation.reason or "sin_motivo"] += 1
    return report

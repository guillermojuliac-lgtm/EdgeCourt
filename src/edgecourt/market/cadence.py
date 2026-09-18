"""Politica de captura hibrida: hitos fijos mas cadencia adaptativa.

Problema que resuelve. Observar cada minuto durante toda la vida de un mercado
multiplica el volumen por 240 y la mayor parte de esas filas son identicas entre
si: a 20 horas del partido el precio no se mueve. Pero observar solo en seis
hitos deja el tramo final -donde se forma el precio de cierre y donde se juega
el CLV- practicamente sin datos.

Politica adoptada:

* **Los hitos se capturan siempre**, tenga o no liquidez el mercado. Un mercado
  vacio a 24 horas es informacion valida: es justo lo que permite medir cuando
  empieza a aparecer la liquidez.
* **La cadencia adaptativa solo se activa cuando el mercado ya ha mostrado
  precios o liquidez.** Antes de eso no hay nada que seguir de cerca, y seguir
  observando un libro vacio cada minuto solo gasta cuota de API.
* **La frecuencia aumenta al acercarse el inicio**, porque es cuando el precio
  se mueve.

Todo es configurable: los tramos son datos, no codigo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from edgecourt.market.snapshots import (
    SNAPSHOT_TARGETS,
    SNAPSHOT_TOLERANCE,
    PlannedSnapshot,
)

# Etiqueta de las capturas adaptativas, para distinguirlas de los hitos.
CONTINUOUS_LABEL: Final[str] = "adaptive"


@dataclass(frozen=True, slots=True)
class CadenceRule:
    """A menos de `within_minutes` del inicio, observar cada `every_minutes`."""

    within_minutes: float
    every_minutes: float


# Tramos por defecto. Se leen de arriba abajo y gana el primero que aplica.
#
# Con estos valores, un mercado con liquidez genera como mucho:
#   10 min / 1  = 10 observaciones en los ultimos 10 minutos
#   20 min / 5  =  4  entre 10 y 30
#   60 min / 10 =  6  entre 30 y 90
#  270 min / 30 =  9  entre 90 y 360
# Total ~29 observaciones adaptativas, frente a las 1.440 de observar cada
# minuto durante 24 horas. Dos ordenes de magnitud menos por la parte que importa.
DEFAULT_CADENCE: Final[tuple[CadenceRule, ...]] = (
    CadenceRule(within_minutes=10, every_minutes=1),
    CadenceRule(within_minutes=30, every_minutes=5),
    CadenceRule(within_minutes=90, every_minutes=10),
    CadenceRule(within_minutes=360, every_minutes=30),
)


@dataclass(frozen=True, slots=True)
class MarketState:
    """Lo que ya sabemos de un mercado, leido de la base de datos."""

    market_id: str
    has_shown_liquidity: bool = False
    has_shown_prices: bool = False
    last_observed_at: datetime | None = None
    captured_labels: frozenset[str] = frozenset()

    @property
    def is_active(self) -> bool:
        """Un mercado 'activo' es el que ya ha mostrado algo que seguir."""
        return self.has_shown_prices or self.has_shown_liquidity


def cadence_for(
    minutes_to_start: float, rules: tuple[CadenceRule, ...] = DEFAULT_CADENCE
) -> float | None:
    """Intervalo de observacion aplicable, o None si aun no toca seguir el mercado."""
    for rule in sorted(rules, key=lambda r: r.within_minutes):
        if minutes_to_start <= rule.within_minutes:
            return rule.every_minutes
    return None


def adaptive_capture_key(observed_at: datetime, interval_minutes: float) -> str:
    """Clave de captura alineada a la rejilla del intervalo.

    Alinear a la rejilla es lo que hace la escritura idempotente: dos ciclos del
    collector dentro del mismo tramo de cinco minutos producen la misma clave y
    la segunda escritura actualiza en lugar de duplicar.
    """
    minutes = int(observed_at.timestamp() // 60)
    bucket = minutes - (minutes % max(int(interval_minutes), 1))
    stamp = datetime.fromtimestamp(bucket * 60, tz=UTC)
    return f"a:{stamp.strftime('%Y%m%d%H%M')}"


def milestone_due(minutes_left: float, captured_labels: frozenset[str]) -> str | None:
    """Hito vencido y no capturado todavia, si lo hay."""
    for label, target in SNAPSHOT_TARGETS.items():
        if label in captured_labels:
            continue
        tolerance = SNAPSHOT_TOLERANCE[label]
        if target - tolerance <= minutes_left <= target + tolerance:
            return label
    return None


def plan_captures(
    catalogues: list[dict],
    states: dict[str, MarketState],
    *,
    now: datetime | None = None,
    rules: tuple[CadenceRule, ...] = DEFAULT_CADENCE,
    adaptive_enabled: bool = True,
) -> list[tuple[PlannedSnapshot, str]]:
    """Decide que capturar ahora. Devuelve pares (captura, capture_key).

    Un mercado puede generar como mucho una captura por ciclo: si coinciden un
    hito y la cadencia adaptativa, **gana el hito**, porque es el que garantiza
    la comparabilidad entre mercados.
    """
    now = now or datetime.now(UTC)
    planned: list[tuple[PlannedSnapshot, str]] = []

    for catalogue in catalogues:
        market_id = catalogue.get("marketId")
        raw_start = catalogue.get("marketStartTime")
        if not market_id or not raw_start:
            continue

        start_time = _parse_start(raw_start)
        if start_time is None:
            continue

        minutes_left = (start_time - now).total_seconds() / 60.0
        if minutes_left < 0:
            continue

        state = states.get(market_id, MarketState(market_id=market_id))

        label = milestone_due(minutes_left, state.captured_labels)
        if label is not None:
            planned.append(
                (
                    PlannedSnapshot(market_id=market_id, label=label, market_start_time=start_time),
                    label,
                )
            )
            continue

        if not adaptive_enabled or not state.is_active:
            continue

        interval = cadence_for(minutes_left, rules)
        if interval is None:
            continue

        # Respetar el intervalo: si la ultima observacion es demasiado reciente,
        # no volver a pedir precios.
        if state.last_observed_at is not None:
            elapsed = (now - state.last_observed_at).total_seconds() / 60.0
            if elapsed < interval:
                continue

        planned.append(
            (
                PlannedSnapshot(
                    market_id=market_id,
                    label=CONTINUOUS_LABEL,
                    market_start_time=start_time,
                ),
                adaptive_capture_key(now, interval),
            )
        )

    return planned


def _parse_start(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def estimate_daily_observations(
    markets_per_day: int, rules: tuple[CadenceRule, ...] = DEFAULT_CADENCE
) -> dict[str, int]:
    """Estimacion de volumen, para dimensionar retencion y almacenamiento."""
    milestones = len(SNAPSHOT_TARGETS)

    adaptive = 0
    previous = 0.0
    for rule in sorted(rules, key=lambda r: r.within_minutes):
        span = rule.within_minutes - previous
        adaptive += int(span / rule.every_minutes)
        previous = rule.within_minutes

    per_market = milestones + adaptive
    return {
        "milestones_per_market": milestones,
        "adaptive_per_market_max": adaptive,
        "observations_per_market_max": per_market,
        "observations_per_day_max": per_market * markets_per_day,
        "runner_price_rows_per_day_max": per_market * markets_per_day * 2,
    }

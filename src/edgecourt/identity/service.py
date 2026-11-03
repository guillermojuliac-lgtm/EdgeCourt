"""Resolucion de identidades por mercado (funciones puras, sin acceso a la base)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from edgecourt.identity import players, tournaments
from edgecourt.identity.players import PlayerIndex, Resolution
from edgecourt.identity.tournaments import SurfaceIndex, SurfaceResolution

READY = "listo"
OUT_OF_SCOPE = "fuera_de_alcance"
DOUBLES = "dobles"
UNRESOLVED_PLAYER = "jugador_sin_resolver"
UNRESOLVED_SURFACE = "superficie_sin_resolver"
SAME_PLAYER = "mismo_jugador"
BAD_SHAPE = "runners_inesperados"


@dataclass(slots=True)
class MarketIdentity:
    market_id: str
    competition: str
    status: str
    surface: str | None = None
    runners: list[tuple[int, str, Resolution]] = field(default_factory=list)


@dataclass(slots=True)
class IdentityReport:
    markets: list[MarketIdentity]
    market_status: Counter
    runner_status: Counter
    unresolved: dict[str, Resolution]  # nombre de Betfair -> resolucion (solo en alcance)

    @property
    def ready(self) -> int:
        return self.market_status.get(READY, 0)


def resolve_markets(
    runners: list[dict[str, Any]], player_index: PlayerIndex, surface_index: SurfaceIndex
) -> IdentityReport:
    """Clasifica cada mercado: listo para predecir o descartado, con el motivo."""
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runners:
        by_market[row["market_id"]].append(row)

    markets: list[MarketIdentity] = []
    market_status: Counter = Counter()
    runner_status: Counter = Counter()
    unresolved: dict[str, Resolution] = {}

    for market_id, rows in by_market.items():
        competition = rows[0]["competition_name"] or ""
        surface: SurfaceResolution = surface_index.resolve(competition)
        item = MarketIdentity(market_id, competition, READY, surface.surface)

        if surface.status == tournaments.OUT_OF_SCOPE:
            item.status = OUT_OF_SCOPE
        elif any(players.is_doubles_name(r["runner_name"]) for r in rows):
            item.status = DOUBLES
        else:
            resolutions = [
                (r["selection_id"], r["runner_name"], player_index.resolve(r["runner_name"]))
                for r in rows
            ]
            item.runners = resolutions
            for _, name, resolution in resolutions:
                runner_status[resolution.status] += 1
                if resolution.status != players.RESOLVED:
                    unresolved.setdefault(name, resolution)
            ids = [res.player_id for _, _, res in resolutions]
            if len(rows) != 2:
                item.status = BAD_SHAPE
            elif any(res.status != players.RESOLVED for _, _, res in resolutions):
                item.status = UNRESOLVED_PLAYER
            elif ids[0] == ids[1]:
                item.status = SAME_PLAYER
            elif surface.status != tournaments.RESOLVED:
                item.status = UNRESOLVED_SURFACE
        markets.append(item)
        market_status[item.status] += 1

    return IdentityReport(markets, market_status, runner_status, unresolved)

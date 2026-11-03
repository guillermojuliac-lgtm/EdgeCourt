"""Superficie de un torneo a partir del nombre de la competicion de Betfair.

«ATP Shanghai 2026» -> clave «shanghai» -> superficie de las ultimas ediciones del
dataset (TML: «Shanghai Masters», «Shanghai»...). Solo se resuelve si las
ediciones recientes coinciden en una unica superficie; si no, queda sin resolver.
Los torneos fuera de «ATP <ciudad> <ano>» (Copa Davis, Laver Cup, ITF, WTA,
Grand Slams) quedan **fuera de alcance** en la v1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from edgecourt.identity.players import normalise_name

# Palabras genericas que TML anade o quita segun la edicion.
_GENERIC = frozenset({"atp", "masters", "open", "tour", "finals", "international"})
_COMPETITION = re.compile(r"^ATP (?P<city>.+) (?P<year>\d{4})$")

# Alias de torneos revisados por una persona: clave de Betfair -> clave del dataset.
ALIASES: dict[str, str] = {}

RESOLVED = "resuelto"
OUT_OF_SCOPE = "fuera_de_alcance"
UNKNOWN = "torneo_desconocido"
AMBIGUOUS = "superficie_ambigua"


def tournament_key(name: str) -> str:
    """Clave normalizada de un torneo (sin palabras genericas ni ano)."""
    words = [w for w in normalise_name(name).split(" ") if w and not w.isdigit()]
    return " ".join(w for w in words if w not in _GENERIC)


@dataclass(frozen=True, slots=True)
class SurfaceResolution:
    status: str
    surface: str | None = None
    key: str | None = None


class SurfaceIndex:
    def __init__(self, surfaces_by_key: dict[str, set[str]]) -> None:
        self._surfaces = surfaces_by_key

    @classmethod
    def build(cls, matches: pd.DataFrame, *, recent_years: int = 3) -> SurfaceIndex:
        """`matches`: columnas date, tourney_name y surface."""
        frame = matches[["date", "tourney_name", "surface"]].dropna(
            subset=["tourney_name", "surface"]
        )
        frame = frame.assign(year=pd.to_datetime(frame["date"]).dt.year)
        frame = frame[frame["year"] > frame["year"].max() - recent_years]
        surfaces: dict[str, set[str]] = {}
        for row in frame.drop_duplicates(["year", "tourney_name", "surface"]).itertuples():
            surfaces.setdefault(tournament_key(row.tourney_name), set()).add(row.surface)
        return cls(surfaces)

    def resolve(self, competition_name: str) -> SurfaceResolution:
        match = _COMPETITION.match(competition_name.strip())
        if match is None:
            return SurfaceResolution(OUT_OF_SCOPE)
        key = tournament_key(match.group("city"))
        key = ALIASES.get(key, key)
        found = self._surfaces.get(key)
        if not found:
            return SurfaceResolution(UNKNOWN, None, key)
        if len(found) > 1:
            return SurfaceResolution(AMBIGUOUS, None, key)
        return SurfaceResolution(RESOLVED, next(iter(found)), key)

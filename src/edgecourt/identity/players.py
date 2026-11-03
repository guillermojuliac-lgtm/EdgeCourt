"""Emparejamiento de jugadores de Betfair con los del dataset.

Reglas, en este orden, **deterministas y sin fuzzy silencioso**:

1. el alias explicito (`ALIASES`), revisado por una persona, manda;
2. coincidencia **exacta** del nombre normalizado (sin acentos, mayusculas,
   guiones ni puntuacion) entre los jugadores ACTIVOS del dataset;
3. si hay exactamente un candidato, se resuelve; si hay varios, es **ambiguo**;
   si no hay ninguno, queda **sin coincidencia**.

Lo dudoso se descarta y se cuenta; nunca se adivina. Para ayudar a revisar los
descartes se listan candidatos por apellido, pero **no se aplican**.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

# Alias revisados por una persona: nombre normalizado de Betfair -> id del dataset.
# Se anade aqui SOLO tras confirmar la identidad. No se infiere.
ALIASES: dict[str, str] = {
    # Confirmado por el responsable el 2026-11-03: «Manuel Cerundolo» en Betfair es
    # Juan Manuel Cerundolo en el dataset (el otro candidato era Francisco Cerundolo).
    "Manuel Cerundolo": "C0C8",
}

ACTIVE_WINDOW_DAYS = 3 * 365

RESOLVED = "resuelto"
AMBIGUOUS = "ambiguo"
UNMATCHED = "sin_coincidencia"
DOUBLES = "dobles"


def normalise_name(name: str) -> str:
    """Minusculas, sin acentos, guiones ni puntuacion, espacios colapsados."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("-", " ").replace("'", "").replace(".", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_doubles_name(name: str) -> bool:
    """Los runners de dobles de Betfair van como «Apellido/Apellido»."""
    return "/" in name


@dataclass(frozen=True, slots=True)
class Resolution:
    status: str
    player_id: str | None = None
    candidates: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (id, nombre)
    via: str | None = None  # "alias" o "exacto"


class PlayerIndex:
    """Indice de jugadores activos del dataset por nombre normalizado."""

    def __init__(
        self,
        by_name: dict[str, dict[str, str]],
        by_surname: dict[str, dict[str, str]],
        known_ids: set[str],
        aliases: dict[str, str],
    ) -> None:
        self._by_name = by_name
        self._by_surname = by_surname
        self._known_ids = known_ids
        self._aliases = aliases

    @classmethod
    def build(
        cls,
        matches: pd.DataFrame,
        *,
        active_window_days: int = ACTIVE_WINDOW_DAYS,
        aliases: dict[str, str] | None = None,
    ) -> PlayerIndex:
        """`matches`: columnas date y player_{a,b}_{id,name}."""
        sides = [
            matches[["date", f"player_{s}_id", f"player_{s}_name"]].set_axis(
                ["date", "id", "name"], axis=1
            )
            for s in ("a", "b")
        ]
        players = pd.concat(sides, ignore_index=True).dropna(subset=["id", "name"])
        players["date"] = pd.to_datetime(players["date"])
        last = players.groupby(["id", "name"], as_index=False)["date"].max()
        horizon = last["date"].max() - pd.Timedelta(days=active_window_days)
        active = last[last["date"] >= horizon]

        by_name: dict[str, dict[str, str]] = {}
        by_surname: dict[str, dict[str, str]] = {}
        for row in active.itertuples(index=False):
            key = normalise_name(row.name)
            by_name.setdefault(key, {})[row.id] = row.name
            by_surname.setdefault(key.split(" ")[-1], {})[row.id] = row.name
        source = ALIASES if aliases is None else aliases
        normalised = {normalise_name(k): v for k, v in source.items()}
        return cls(by_name, by_surname, set(last["id"]), normalised)

    def resolve(self, betfair_name: str) -> Resolution:
        if is_doubles_name(betfair_name):
            return Resolution(DOUBLES)
        key = normalise_name(betfair_name)

        alias = self._aliases.get(key)
        if alias is not None:
            if alias in self._known_ids:
                return Resolution(RESOLVED, alias, via="alias")
            return Resolution(UNMATCHED)  # alias a un id que ya no existe: no se adivina

        candidates = self._by_name.get(key, {})
        if len(candidates) == 1:
            ((pid, name),) = candidates.items()
            return Resolution(RESOLVED, pid, ((pid, name),), via="exacto")
        if len(candidates) > 1:
            return Resolution(AMBIGUOUS, None, tuple(sorted(candidates.items())))

        # Sin coincidencia: se sugieren candidatos por apellido SOLO para revisar.
        surname = key.split(" ")[-1]
        hints = tuple(sorted(self._by_surname.get(surname, {}).items()))
        return Resolution(UNMATCHED, None, hints)

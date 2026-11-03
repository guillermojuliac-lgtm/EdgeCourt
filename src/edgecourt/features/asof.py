"""Features de un partido FUTURO «a fecha de hoy» (Carril A, paso A1).

La pasada por lotes (`builder.build_features`) calcula, para cada partido
historico, las features con el estado previo a ese partido. Predecir un partido
que aun no se ha jugado es lo mismo con el estado FINAL: este modulo conserva ese
estado y reutiliza **la misma funcion de emision** (`builder._emit_features`) y el
mismo Elo, de modo que el calculo «hoy» y el de lotes no pueden divergir.

Garantias:

* **Sin fuga temporal.** Se rechaza una fecha anterior al ultimo partido del
  estado: pedir features «en el pasado» con datos posteriores sería fuga.
* **Atributos de jugador.** El batch lee ranking, puntos, edad y altura de la fila
  del propio partido. Para un partido futuro no existe esa fila: se usan los
  ULTIMOS valores conocidos de cada jugador (de su ultimo partido), avanzando la
  edad hasta la fecha pedida. Es una aproximacion (no es el ranking semanal
  actual) y se declara en el resultado (`attribute_age_days`). Quien llama puede
  pasar los atributos exactos con `attributes=`.
* Un jugador desconocido produce features vacias (NaN) y experiencia 0; decidir
  si se apuesta es responsabilidad de quien llama (historial minimo).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from edgecourt.data import schema
from edgecourt.features.builder import (
    FEATURE_NAMES,
    FeatureState,
    PlayerHistory,
    _emit_features,
    build_features_with_state,
)
from edgecourt.features.elo import EloConfig, EloState, compute_elo_state, expected_score

# Features que se calculan fuera del constructor (se unen desde el Elo).
ELO_FEATURE_NAMES: tuple[str, ...] = ("elo_diff", "surface_elo_diff")
ALL_FEATURE_NAMES: tuple[str, ...] = FEATURE_NAMES + ELO_FEATURE_NAMES

_ATTRIBUTE_DIFFS: tuple[tuple[str, str], ...] = (
    ("ranking_diff", "rank"),
    ("rank_points_diff", "rank_points"),
    ("age_diff", "age"),
    ("height_diff", "height"),
)


@dataclass(slots=True)
class AsOfState:
    """Estado acumulado tras recorrer todos los partidos conocidos."""

    features: FeatureState
    elo: EloState
    data_as_of: pd.Timestamp | None
    n_matches: int


@dataclass(slots=True)
class AsOfFeatures:
    """Features de un partido futuro y la informacion necesaria para fiarse de ellas."""

    values: dict[str, float]
    player_a_matches_before: int
    player_b_matches_before: int
    elo_a: float
    elo_b: float
    elo_probability_a: float
    attribute_source: str  # "latest" o "provided"
    attribute_age_days: dict[str, float | None]
    data_as_of: pd.Timestamp | None

    def vector(self, feature_names: list[str] | tuple[str, ...]) -> list[float]:
        """Valores en el orden que pide el modelo."""
        missing = [name for name in feature_names if name not in self.values]
        if missing:
            raise KeyError(f"features no disponibles: {missing}")
        return [self.values[name] for name in feature_names]


def build_asof_state(matches: pd.DataFrame, elo_config: EloConfig | None = None) -> AsOfState:
    """Recorre `matches` (orden cronologico total) y conserva el estado final."""
    _, feature_state = build_features_with_state(matches)
    elo_state = compute_elo_state(matches, elo_config)
    data_as_of = pd.to_datetime(matches["date"]).max() if len(matches) else None
    return AsOfState(
        features=feature_state,
        elo=elo_state,
        data_as_of=None if data_as_of is None or pd.isna(data_as_of) else pd.Timestamp(data_as_of),
        n_matches=len(matches),
    )


def _latest_attributes(
    state: FeatureState, player: str, as_of: pd.Timestamp
) -> tuple[dict[str, float], float | None]:
    """Ultimos atributos conocidos, con la edad avanzada hasta `as_of`."""
    info = state.latest.get(player)
    if info is None:
        return {k: np.nan for _, k in _ATTRIBUTE_DIFFS}, None
    out: dict[str, float] = {}
    for _, key in _ATTRIBUTE_DIFFS:
        value = info.get(key)
        out[key] = float(value) if value is not None and pd.notna(value) else np.nan
    age_days: float | None = None
    last_date = info.get("date")
    if last_date is not None and pd.notna(last_date):
        age_days = float((as_of - pd.Timestamp(last_date)).days)
        if np.isfinite(out["age"]):
            out["age"] = out["age"] + age_days / 365.25
    return out, age_days


def features_for_pair(
    state: AsOfState,
    player_a: str,
    player_b: str,
    *,
    surface: str | None,
    as_of: pd.Timestamp,
    attributes: dict[str, dict[str, float]] | None = None,
) -> AsOfFeatures:
    """Features A - B de un partido que se jugara en `as_of`.

    Args:
        state: estado construido con `build_asof_state`.
        player_a, player_b: identificadores de jugador del dataset (no de Betfair).
        surface: `hard`, `clay`, `grass`, `carpet` o `None` si se desconoce.
        as_of: fecha del partido. No puede ser anterior al ultimo partido del estado.
        attributes: atributos exactos `{"a": {...}, "b": {...}}` con `rank`,
            `rank_points`, `age` y `height`; si se omiten, se usan los ultimos conocidos.
    """
    as_of = pd.Timestamp(as_of)
    if state.data_as_of is not None and as_of < state.data_as_of:
        raise ValueError(
            f"as_of ({as_of.date()}) es anterior al ultimo partido del estado "
            f"({state.data_as_of.date()}): usarlo seria fuga temporal"
        )
    if player_a == player_b:
        raise ValueError("los dos jugadores son el mismo")

    fstate = state.features
    history_a = fstate.histories.get(player_a) or PlayerHistory()
    history_b = fstate.histories.get(player_b) or PlayerHistory()

    if attributes is not None:
        attrs_a, attrs_b = attributes["a"], attributes["b"]
        source = "provided"
        ages: dict[str, float | None] = {"a": None, "b": None}
    else:
        attrs_a, age_a = _latest_attributes(fstate, player_a, as_of)
        attrs_b, age_b = _latest_attributes(fstate, player_b, as_of)
        source = "latest"
        ages = {"a": age_a, "b": age_b}

    attribute_diffs = {
        name: _diff(attrs_a.get(key), attrs_b.get(key)) for name, key in _ATTRIBUTE_DIFFS
    }
    values = _emit_features(
        attribute_diffs,
        history_a,
        history_b,
        fstate.h2h,
        player_a,
        player_b,
        surface,
        as_of,
    )

    overall = state.elo.overall
    elo_a, elo_b = overall.rating(player_a), overall.rating(player_b)
    values["elo_diff"] = elo_a - elo_b
    book = state.elo.by_surface.get(surface) if isinstance(surface, str) else None
    if book is not None and surface in schema.ELO_SURFACES:
        values["surface_elo_diff"] = book.rating(player_a) - book.rating(player_b)
    else:
        values["surface_elo_diff"] = np.nan

    return AsOfFeatures(
        values=values,
        player_a_matches_before=history_a.total_matches,
        player_b_matches_before=history_b.total_matches,
        elo_a=elo_a,
        elo_b=elo_b,
        elo_probability_a=expected_score(elo_a, elo_b, state.elo.config.scale),
        attribute_source=source,
        attribute_age_days=ages,
        data_as_of=state.data_as_of,
    )


def _diff(a: float | None, b: float | None) -> float:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return np.nan
    return float(a) - float(b)

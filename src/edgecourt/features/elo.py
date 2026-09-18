"""Elo global y Elo por superficie.

Es el primer benchmark serio del proyecto: un modelo de ML que no supere al Elo
de forma estable no justifica su complejidad (docs/MODELS.md).

Garantia central (riesgo R2 del plan): **una sola pasada cronologica**. Para
cada partido se persiste el estado *previo* de ambos jugadores, y solo despues
se actualizan los ratings. Es imposible por construccion que el rating usado
para predecir un partido incorpore su resultado.

Decisiones de diseno:

* **K dinamico**, no constante. Un jugador con 5 partidos tiene un rating mucho
  mas incierto que uno con 500; aplicarles el mismo K haria que el veterano
  oscilase demasiado y que el novato convergiese demasiado despacio. Se usa
  `K = k_base / (n + k_shift) ** k_decay`, la forma habitual en tenis.
* **Los walkovers no actualizan el rating.** No se jugo un partido, asi que no
  hay informacion sobre quien es mejor. Los abandonos (`retired`) si actualizan:
  hubo partido y hubo un ganador.
* **El Elo de superficie se lleva aparte** y solo lo alimentan los partidos de
  esa superficie. Carpet queda fuera (circuito extinto, muestra residual): esos
  partidos actualizan el Elo global pero ningun Elo de superficie.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from edgecourt.data import schema
from edgecourt.logging_setup import get_logger

log = get_logger("features.elo")

INITIAL_RATING = 1500.0

# Estados que no aportan informacion sobre quien juega mejor.
NON_INFORMATIVE_STATUSES = frozenset({"walkover", "unknown"})


@dataclass(frozen=True, slots=True)
class EloConfig:
    """Parametros del sistema Elo.

    Los valores por defecto son los habituales en tenis. No se han ajustado
    sobre los datos: hacerlo requeriria buscar sobre VALIDATION y registrarlo,
    y para un benchmark es preferible un parametro estandar y honesto.
    """

    initial_rating: float = INITIAL_RATING
    k_base: float = 250.0
    k_shift: float = 5.0
    k_decay: float = 0.4
    # Escala clasica de Elo: 400 puntos = 10 a 1 en cuota.
    scale: float = 400.0

    def k_factor(self, matches_played: int) -> float:
        return self.k_base / ((matches_played + self.k_shift) ** self.k_decay)


@dataclass(slots=True)
class _RatingBook:
    """Ratings y numero de partidos por jugador."""

    initial: float
    ratings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def rating(self, player: str) -> float:
        return self.ratings.get(player, self.initial)

    def count(self, player: str) -> int:
        return self.counts.get(player, 0)

    def update(self, player: str, new_rating: float) -> None:
        self.ratings[player] = new_rating
        self.counts[player] = self.counts.get(player, 0) + 1


def expected_score(rating_a: float, rating_b: float, scale: float = 400.0) -> float:
    """Probabilidad de que A gane, segun la formula logistica de Elo."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / scale))


def expected_score_array(diff: np.ndarray | pd.Series, scale: float = 400.0) -> np.ndarray:
    """Version vectorizada sobre la diferencia de rating (A - B)."""
    return 1.0 / (1.0 + np.power(10.0, -np.asarray(diff, dtype=float) / scale))


OUTPUT_COLUMNS: tuple[str, ...] = (
    "match_id",
    "date",
    "year",
    "surface",
    "elo_a_before",
    "elo_b_before",
    "elo_diff",
    "elo_a_matches_before",
    "elo_b_matches_before",
    "surface_elo_a_before",
    "surface_elo_b_before",
    "surface_elo_diff",
    "surface_elo_a_matches_before",
    "surface_elo_b_matches_before",
)


def _check_chronological(df: pd.DataFrame) -> None:
    """Aborta si el dataframe no viene en el orden total definido en la ingesta.

    No es una comprobacion defensiva menor: si el orden esta roto, todo lo que
    calcule este modulo es invalido, y en silencio.
    """
    keys = ["date", "tourney_id", "round_order", "match_num"]
    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas de la clave de orden: {missing}")

    ordered = df[keys].sort_values(keys, kind="mergesort", na_position="last")
    if not ordered.index.equals(df.index):
        raise ValueError(
            "El dataset no esta en orden cronologico. El Elo solo puede calcularse "
            "sobre la clave de orden total (date, tourney_id, round_order, match_num)."
        )


def compute_elo(df: pd.DataFrame, config: EloConfig | None = None) -> pd.DataFrame:
    """Calcula Elo global y por superficie en una unica pasada cronologica.

    Args:
        df: `match_facts` ordenado por la clave de orden total.
        config: parametros del sistema.

    Returns:
        Un DataFrame con una fila por partido y el estado **previo** de los
        ratings. Nunca contiene el estado posterior: ese es el punto.
    """
    config = config or EloConfig()
    _check_chronological(df)

    overall = _RatingBook(initial=config.initial_rating)
    by_surface: dict[str, _RatingBook] = {
        surface: _RatingBook(initial=config.initial_rating) for surface in schema.ELO_SURFACES
    }

    n = len(df)
    elo_a = np.empty(n, dtype=float)
    elo_b = np.empty(n, dtype=float)
    count_a = np.zeros(n, dtype=np.int32)
    count_b = np.zeros(n, dtype=np.int32)
    s_elo_a = np.full(n, np.nan, dtype=float)
    s_elo_b = np.full(n, np.nan, dtype=float)
    s_count_a = np.zeros(n, dtype=np.int32)
    s_count_b = np.zeros(n, dtype=np.int32)

    players_a = df["player_a_id"].to_numpy(dtype=object)
    players_b = df["player_b_id"].to_numpy(dtype=object)
    targets = df["target"].to_numpy(dtype=float)
    surfaces = df["surface"].to_numpy(dtype=object)
    statuses = df["completion_status"].to_numpy(dtype=object)

    skipped = 0
    for i in range(n):
        a, b = players_a[i], players_b[i]
        surface = surfaces[i]
        book = by_surface.get(surface) if isinstance(surface, str) else None

        # --- Estado PREVIO: se registra antes de tocar nada ------------------
        rating_a, rating_b = overall.rating(a), overall.rating(b)
        elo_a[i], elo_b[i] = rating_a, rating_b
        count_a[i], count_b[i] = overall.count(a), overall.count(b)

        if book is not None:
            surface_a, surface_b = book.rating(a), book.rating(b)
            s_elo_a[i], s_elo_b[i] = surface_a, surface_b
            s_count_a[i], s_count_b[i] = book.count(a), book.count(b)

        # --- Actualizacion ---------------------------------------------------
        if statuses[i] in NON_INFORMATIVE_STATUSES or pd.isna(targets[i]):
            skipped += 1
            continue
        if not isinstance(a, str) or not isinstance(b, str):
            skipped += 1
            continue

        outcome_a = float(targets[i])
        expected_a = expected_score(rating_a, rating_b, config.scale)
        k_a = config.k_factor(overall.count(a))
        k_b = config.k_factor(overall.count(b))
        overall.update(a, rating_a + k_a * (outcome_a - expected_a))
        overall.update(b, rating_b + k_b * ((1.0 - outcome_a) - (1.0 - expected_a)))

        if book is not None:
            expected_surface_a = expected_score(surface_a, surface_b, config.scale)
            k_surface_a = config.k_factor(book.count(a))
            k_surface_b = config.k_factor(book.count(b))
            book.update(a, surface_a + k_surface_a * (outcome_a - expected_surface_a))
            book.update(
                b,
                surface_b + k_surface_b * ((1.0 - outcome_a) - (1.0 - expected_surface_a)),
            )

    result = pd.DataFrame(
        {
            "match_id": df["match_id"].to_numpy(),
            "date": df["date"].to_numpy(),
            "year": df["year"].to_numpy(),
            "surface": df["surface"].to_numpy(),
            "elo_a_before": elo_a,
            "elo_b_before": elo_b,
            "elo_diff": elo_a - elo_b,
            "elo_a_matches_before": count_a,
            "elo_b_matches_before": count_b,
            "surface_elo_a_before": s_elo_a,
            "surface_elo_b_before": s_elo_b,
            "surface_elo_diff": s_elo_a - s_elo_b,
            "surface_elo_a_matches_before": s_count_a,
            "surface_elo_b_matches_before": s_count_b,
        }
    )

    log.info(
        "elo calculado",
        extra={
            "matches": n,
            "skipped_updates": skipped,
            "players": len(overall.ratings),
            "surfaces": {s: len(book.ratings) for s, book in by_surface.items()},
        },
    )
    return result[list(OUTPUT_COLUMNS)]


def elo_probability(
    elo_features: pd.DataFrame,
    *,
    surface_weight: float = 0.0,
    config: EloConfig | None = None,
) -> pd.Series:
    """Probabilidad de que gane el jugador A, a partir de los ratings previos.

    `surface_weight` mezcla ambas diferencias de rating **en espacio de Elo**,
    no de probabilidad: es donde la mezcla es lineal y tiene sentido.
    Con 0.0 se usa solo el Elo global; con 1.0, solo el de superficie.

    Cuando falta el Elo de superficie (partidos en carpet, o superficie nula) se
    recurre al Elo global en lugar de inventar un valor.
    """
    config = config or EloConfig()
    if not 0.0 <= surface_weight <= 1.0:
        raise ValueError(f"surface_weight debe estar en [0, 1], recibido {surface_weight}")

    overall_diff = elo_features["elo_diff"].astype(float)
    surface_diff = elo_features["surface_elo_diff"].astype(float)
    blended = (1.0 - surface_weight) * overall_diff + surface_weight * surface_diff
    blended = blended.where(surface_diff.notna(), overall_diff)

    return pd.Series(
        expected_score_array(blended, config.scale), index=elo_features.index, name="probability"
    )

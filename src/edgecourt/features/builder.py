"""Generacion de features, sin data leakage por construccion.

Este es el modulo de mayor riesgo del proyecto. Un error aqui no produce un
fallo visible: produce un modelo con AUC de 0,99 y valor predictivo cero.

**Arquitectura anti-leakage.** Una unica pasada cronologica con un estado por
jugador. Para cada partido, en este orden estricto:

1. se leen las features a partir del estado acumulado -que solo contiene
   partidos anteriores-;
2. se emiten esas features;
3. **solo entonces** se incorpora el partido actual al estado.

De este modo es imposible que una feature del partido T incorpore informacion de
T o posterior, y no depende de recordar aplicar un `shift`: depende del orden de
las operaciones, que esta fijado por tests de envenenamiento.

**Las columnas `raw_` del partido en curso nunca se leen para construir sus
propias features.** Se usan exclusivamente para alimentar el historial *despues*
de haber emitido las features de ese partido.

Todas las features de rendimiento son **relativas A - B** y antisimetricas:
intercambiar los lados invierte el signo.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Final

import numpy as np
import pandas as pd

from edgecourt.data import schema
from edgecourt.logging_setup import get_logger

log = get_logger("features.builder")

# Ventanas de forma reciente (numero de partidos).
FORM_WINDOWS: Final[tuple[int, ...]] = (5, 10, 20)

# Ventana para las medias moviles de estadisticas de servicio y resto.
# 20 partidos equivale aproximadamente a media temporada de un jugador de tour:
# suficiente para promediar el ruido, corto para seguir reflejando el estado actual.
STAT_WINDOW: Final[int] = 20

# Ventanas de carga de trabajo (en dias).
LOAD_WINDOWS: Final[tuple[int, ...]] = (7, 14)

# Metricas de rendimiento derivadas de las estadisticas de un partido.
STAT_NAMES: Final[tuple[str, ...]] = (
    "first_serve_pct",
    "first_serve_points_won",
    "second_serve_points_won",
    "return_points_won",
    "break_points_saved",
    "break_points_converted",
    "aces_rate",
    "double_fault_rate",
)

# Estados que no describen un partido jugado: no alimentan el historial.
NON_INFORMATIVE_STATUSES: Final[frozenset[str]] = frozenset({"walkover", "unknown"})


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Division que devuelve NaN en lugar de romperse o inventar un cero."""
    if denominator is None or numerator is None:
        return np.nan
    if not np.isfinite(denominator) or not np.isfinite(numerator) or denominator <= 0:
        return np.nan
    return numerator / denominator


def match_statistics(player: dict[str, float], opponent: dict[str, float]) -> dict[str, float]:
    """Metricas de rendimiento de un jugador en un partido concreto.

    Se calculan a partir de las columnas `raw_` y sirven **solo** para alimentar
    el historial. Nunca se usan como feature del partido del que proceden.

    Las metricas de resto se derivan de las estadisticas de servicio del rival:
    los puntos que el rival no gano con su saque son los que gano el jugador al
    resto.
    """
    svpt = player.get("svpt", np.nan)
    first_in = player.get("first_in", np.nan)
    opponent_svpt = opponent.get("svpt", np.nan)
    opponent_bp_faced = opponent.get("bp_faced", np.nan)

    second_serves = svpt - first_in if np.isfinite(svpt) and np.isfinite(first_in) else np.nan
    opponent_service_points_won = opponent.get("first_won", np.nan) + opponent.get(
        "second_won", np.nan
    )

    return {
        "first_serve_pct": _safe_ratio(first_in, svpt),
        "first_serve_points_won": _safe_ratio(player.get("first_won", np.nan), first_in),
        "second_serve_points_won": _safe_ratio(player.get("second_won", np.nan), second_serves),
        "return_points_won": _safe_ratio(
            opponent_svpt - opponent_service_points_won, opponent_svpt
        ),
        "break_points_saved": _safe_ratio(
            player.get("bp_saved", np.nan), player.get("bp_faced", np.nan)
        ),
        "break_points_converted": _safe_ratio(
            opponent_bp_faced - opponent.get("bp_saved", np.nan), opponent_bp_faced
        ),
        "aces_rate": _safe_ratio(player.get("aces", np.nan), svpt),
        "double_fault_rate": _safe_ratio(player.get("dfs", np.nan), svpt),
    }


@dataclass(slots=True)
class PlayerHistory:
    """Historial acumulado de un jugador. Solo contiene partidos ya disputados."""

    results: deque[int] = field(default_factory=lambda: deque(maxlen=max(FORM_WINDOWS)))
    surface_results: dict[str, list[int]] = field(default_factory=dict)
    stats: deque[dict[str, float]] = field(default_factory=lambda: deque(maxlen=STAT_WINDOW))
    # (fecha, minutos) de los partidos recientes, para la carga de trabajo.
    recent: deque[tuple[pd.Timestamp, float]] = field(default_factory=deque)
    last_date: pd.Timestamp | None = None
    total_matches: int = 0

    def winrate(self, window: int) -> float:
        """Porcentaje de victorias en los ultimos `window` partidos."""
        if len(self.results) < window:
            return np.nan
        recent = list(self.results)[-window:]
        return sum(recent) / window

    def surface_winrate(self, surface: str | None, minimum: int = 5) -> float:
        """Porcentaje de victorias en una superficie, con minimo de muestra."""
        if surface is None or surface not in self.surface_results:
            return np.nan
        wins, matches = self.surface_results[surface]
        return wins / matches if matches >= minimum else np.nan

    def mean_stat(self, name: str, minimum: int = 3) -> float:
        """Media movil de una metrica sobre los ultimos partidos con datos."""
        values = [s[name] for s in self.stats if np.isfinite(s.get(name, np.nan))]
        return float(np.mean(values)) if len(values) >= minimum else np.nan

    def days_since_last_match(self, date: pd.Timestamp) -> float:
        if self.last_date is None:
            return np.nan
        return float((date - self.last_date).days)

    def _purge(self, date: pd.Timestamp) -> None:
        """Descarta los partidos fuera de la mayor ventana de carga."""
        horizon = max(LOAD_WINDOWS)
        while self.recent and (date - self.recent[0][0]).days > horizon:
            self.recent.popleft()

    def matches_in_last_days(self, date: pd.Timestamp, days: int) -> int:
        self._purge(date)
        return sum(1 for d, _ in self.recent if 0 <= (date - d).days <= days)

    def minutes_in_last_days(self, date: pd.Timestamp, days: int) -> float:
        self._purge(date)
        total = [m for d, m in self.recent if 0 <= (date - d).days <= days and np.isfinite(m)]
        return float(sum(total)) if total else 0.0

    def record(
        self,
        *,
        won: bool,
        surface: str | None,
        stats: dict[str, float],
        date: pd.Timestamp,
        minutes: float,
    ) -> None:
        """Incorpora un partido ya disputado. Se llama DESPUES de emitir features."""
        self.results.append(1 if won else 0)
        if surface is not None:
            bucket = self.surface_results.setdefault(surface, [0, 0])
            bucket[0] += 1 if won else 0
            bucket[1] += 1
        if any(np.isfinite(v) for v in stats.values()):
            self.stats.append(stats)
        if pd.notna(date):
            self.recent.append((date, minutes))
            self.last_date = date if self.last_date is None else max(self.last_date, date)
        self.total_matches += 1


def _feature_names() -> tuple[str, ...]:
    names: list[str] = [
        "ranking_diff",
        "rank_points_diff",
        "age_diff",
        "height_diff",
    ]
    names += [f"winrate_last_{w}_diff" for w in FORM_WINDOWS]
    names.append("surface_winrate_diff")
    names += [f"{stat}_diff" for stat in STAT_NAMES]
    names.append("days_since_last_match_diff")
    names += [f"matches_last_{w}_days_diff" for w in LOAD_WINDOWS]
    names.append("minutes_played_last_7_days_diff")
    names.append("head_to_head_before_match")
    return tuple(names)


FEATURE_NAMES: Final[tuple[str, ...]] = _feature_names()

# Columnas de contexto: no son diferencias entre jugadores, describen el partido.
# Son conocidas antes de jugarse, asi que su uso es legitimo.
CONTEXT_COLUMNS: Final[tuple[str, ...]] = (
    "tourney_level",
    "surface",
    "round",
    "indoor",
    "best_of",
)

# Columnas de experiencia, utiles para filtrar partidos poco informativos.
EXPERIENCE_COLUMNS: Final[tuple[str, ...]] = (
    "player_a_matches_before",
    "player_b_matches_before",
)

OUTPUT_COLUMNS: Final[tuple[str, ...]] = (
    ("match_id", "date", "year") + FEATURE_NAMES + CONTEXT_COLUMNS + EXPERIENCE_COLUMNS
)


def _h2h_key(player_a: str, player_b: str) -> tuple[str, str]:
    return (player_a, player_b) if player_a <= player_b else (player_b, player_a)


def _raw_stats(row: dict, side: str) -> dict[str, float]:
    """Extrae las estadisticas crudas de un lado como diccionario de floats."""
    values: dict[str, float] = {}
    for suffix in schema.RAW_STAT_SUFFIXES:
        value = row.get(f"raw_{side}_{suffix}")
        values[suffix] = float(value) if value is not None and pd.notna(value) else np.nan
    return values


def _check_chronological(df: pd.DataFrame) -> None:
    keys = ["date", "tourney_id", "round_order", "match_num"]
    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas de la clave de orden: {missing}")
    ordered = df[keys].sort_values(keys, kind="mergesort", na_position="last")
    if not ordered.index.equals(df.index):
        raise ValueError(
            "El dataset no esta en orden cronologico. Las features solo pueden generarse "
            "sobre la clave de orden total (date, tourney_id, round_order, match_num)."
        )


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Genera la tabla de features en una unica pasada cronologica.

    Args:
        df: `match_facts` ordenado por la clave de orden total.

    Returns:
        Una fila por partido con las features conocidas **antes** de disputarlo.
        No incluye `target` ni ninguna columna de resultado: para entrenar se une
        por `match_id`, de modo que el resultado nunca viaja con las features.
    """
    _check_chronological(df)

    histories: dict[str, PlayerHistory] = {}
    h2h: dict[tuple[str, str], dict[str, int]] = {}

    n = len(df)
    columns: dict[str, np.ndarray] = {
        name: np.full(n, np.nan, dtype=float) for name in FEATURE_NAMES
    }
    experience_a = np.zeros(n, dtype=np.int32)
    experience_b = np.zeros(n, dtype=np.int32)

    records = df.to_dict(orient="records")
    unusable = 0

    for i, row in enumerate(records):
        player_a, player_b = row["player_a_id"], row["player_b_id"]
        date = row["date"]
        surface = row["surface"] if isinstance(row["surface"], str) else None

        # Un partido sin identificador de jugador no puede generar features ni
        # alimentar ningun historial: se emite la fila en blanco y se ignora.
        # La validacion de la ingesta ya lo marca como error (docs/DATA.md).
        if not isinstance(player_a, str) or not isinstance(player_b, str):
            unusable += 1
            continue

        history_a = histories.setdefault(player_a, PlayerHistory())
        history_b = histories.setdefault(player_b, PlayerHistory())

        # --- 1. LEER el estado previo y emitir features ----------------------
        columns["ranking_diff"][i] = _numeric_diff(row, "rank")
        columns["rank_points_diff"][i] = _numeric_diff(row, "rank_points")
        columns["age_diff"][i] = _numeric_diff(row, "age")
        columns["height_diff"][i] = _numeric_diff(row, "height")

        for window in FORM_WINDOWS:
            columns[f"winrate_last_{window}_diff"][i] = history_a.winrate(
                window
            ) - history_b.winrate(window)

        columns["surface_winrate_diff"][i] = history_a.surface_winrate(
            surface
        ) - history_b.surface_winrate(surface)

        for stat in STAT_NAMES:
            columns[f"{stat}_diff"][i] = history_a.mean_stat(stat) - history_b.mean_stat(stat)

        columns["days_since_last_match_diff"][i] = history_a.days_since_last_match(
            date
        ) - history_b.days_since_last_match(date)

        for window in LOAD_WINDOWS:
            columns[f"matches_last_{window}_days_diff"][i] = history_a.matches_in_last_days(
                date, window
            ) - history_b.matches_in_last_days(date, window)

        columns["minutes_played_last_7_days_diff"][i] = history_a.minutes_in_last_days(
            date, 7
        ) - history_b.minutes_in_last_days(date, 7)

        key = _h2h_key(player_a, player_b)
        record = h2h.get(key)
        if record is None:
            columns["head_to_head_before_match"][i] = 0.0
        else:
            columns["head_to_head_before_match"][i] = float(
                record.get(player_a, 0) - record.get(player_b, 0)
            )

        experience_a[i] = history_a.total_matches
        experience_b[i] = history_b.total_matches

        # --- 2. ACTUALIZAR el estado con el partido ya emitido ---------------
        status = row.get("completion_status")
        target = row.get("target")
        if status in NON_INFORMATIVE_STATUSES or pd.isna(target):
            continue

        a_won = bool(target == 1)
        minutes = float(row["minutes"]) if pd.notna(row.get("minutes")) else np.nan
        raw_a, raw_b = _raw_stats(row, "a"), _raw_stats(row, "b")

        history_a.record(
            won=a_won,
            surface=surface,
            stats=match_statistics(raw_a, raw_b),
            date=date,
            minutes=minutes,
        )
        history_b.record(
            won=not a_won,
            surface=surface,
            stats=match_statistics(raw_b, raw_a),
            date=date,
            minutes=minutes,
        )

        winner = player_a if a_won else player_b
        h2h.setdefault(key, {})[winner] = h2h.setdefault(key, {}).get(winner, 0) + 1

    result = pd.DataFrame(
        {
            "match_id": df["match_id"].to_numpy(),
            "date": df["date"].to_numpy(),
            "year": df["year"].to_numpy(),
            **columns,
            **{column: df[column].to_numpy() for column in CONTEXT_COLUMNS},
            "player_a_matches_before": experience_a,
            "player_b_matches_before": experience_b,
        }
    )

    coverage = {
        name: round(100.0 * float(np.isfinite(columns[name]).mean()), 1) for name in FEATURE_NAMES
    }
    log.info(
        "features generadas",
        extra={
            "matches": n,
            "features": len(FEATURE_NAMES),
            "players": len(histories),
            "unusable_rows": unusable,
        },
    )
    result.attrs["coverage"] = coverage
    return result[list(OUTPUT_COLUMNS)]


def _numeric_diff(row: dict, field_name: str) -> float:
    """Diferencia A - B de un atributo numerico conocido antes del partido."""
    a = row.get(f"player_a_{field_name}")
    b = row.get(f"player_b_{field_name}")
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return np.nan
    return float(a) - float(b)


def feature_coverage(features: pd.DataFrame) -> pd.DataFrame:
    """Porcentaje de partidos con valor para cada feature."""
    rows = [
        {
            "feature": name,
            "coverage_pct": round(100.0 * features[name].notna().mean(), 1),
            "mean": round(float(features[name].mean(skipna=True)), 4),
            "std": round(float(features[name].std(skipna=True)), 4),
        }
        for name in FEATURE_NAMES
    ]
    return pd.DataFrame(rows).sort_values("coverage_pct")

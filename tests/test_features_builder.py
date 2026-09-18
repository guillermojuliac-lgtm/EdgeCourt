"""Tests de la generacion de features.

El bloqueante de PHASE 3 es `test_no_future_data_leakage`. Si falla, todo lo que
se construya encima es invalido y no se avanza de fase.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tests.factories import _raw_frame

from edgecourt.data import ingest, schema
from edgecourt.features.builder import (
    CONTEXT_COLUMNS,
    FEATURE_NAMES,
    OUTPUT_COLUMNS,
    STAT_NAMES,
    PlayerHistory,
    build_features,
    match_statistics,
)


def _facts(entries: list[tuple[str, str, str]], **overrides) -> pd.DataFrame:
    """Secuencia cronologica de (superficie, ganador, perdedor)."""
    rows = []
    for i, (surface, winner, loser) in enumerate(entries):
        row = _raw_frame(1, surface=surface, **overrides).iloc[0].to_dict()
        row["winner_id"], row["loser_id"] = winner, loser
        row["winner_name"], row["loser_name"] = f"P {winner}", f"P {loser}"
        row["tourney_id"] = f"2023-{i:03d}"
        row["tourney_date"] = f"2023{(i // 28) + 1:02d}{(i % 28) + 1:02d}"
        row["match_num"] = str(i + 1)
        rows.append(row)
    return ingest.normalise_raw_matches(pd.DataFrame(rows, dtype=str), source="test")


def _mirror(df: pd.DataFrame) -> pd.DataFrame:
    """Intercambia los lados A y B de todas las filas, coherentemente."""
    swapped = df.copy()
    for field in ("id", "name", "rank", "rank_points", "age", "hand", "height"):
        a, b = f"player_a_{field}", f"player_b_{field}"
        swapped[a], swapped[b] = df[b].copy(), df[a].copy()
    for suffix in schema.RAW_STAT_SUFFIXES:
        a, b = f"raw_a_{suffix}", f"raw_b_{suffix}"
        swapped[a], swapped[b] = df[b].copy(), df[a].copy()
    swapped["sets_a"], swapped["sets_b"] = df["sets_b"].copy(), df["sets_a"].copy()
    swapped["target"] = 1 - df["target"]
    return swapped


# =============================================================================
# BLOQUEANTE: ausencia de data leakage temporal
# =============================================================================


@pytest.mark.critical
def test_no_future_data_leakage():
    """Alterar un partido no puede cambiar las features de ese partido ni de los previos.

    Test de envenenamiento sobre los tres vectores por los que el resultado de un
    partido podria filtrarse a sus propias features:

    * `target`   - quien gano;
    * `raw_*`    - las estadisticas de servicio y resto del partido;
    * `minutes`  - la duracion.

    Para cada partido k se corrompe cada vector y se comprueba que las filas
    0..k conservan exactamente sus features. Si alguna cambiase, el generador
    estaria leyendo el presente o el futuro.
    """
    facts = _facts(
        [
            ("Clay", "A", "B"),
            ("Clay", "B", "C"),
            ("Hard", "C", "A"),
            ("Hard", "A", "C"),
            ("Grass", "B", "A"),
            ("Clay", "C", "B"),
            ("Clay", "A", "B"),
            ("Hard", "B", "C"),
        ]
    )
    baseline = build_features(facts)
    features = list(FEATURE_NAMES)

    for k in range(len(facts)):
        poisons: dict[str, pd.DataFrame] = {}

        flipped = facts.copy()
        flipped.loc[flipped.index[k], "target"] = 1 - flipped.loc[flipped.index[k], "target"]
        poisons["target"] = flipped

        stats = facts.copy()
        for suffix in schema.RAW_STAT_SUFFIXES:
            stats.loc[stats.index[k], f"raw_a_{suffix}"] = 1
            stats.loc[stats.index[k], f"raw_b_{suffix}"] = 999
        poisons["raw_stats"] = stats

        duration = facts.copy()
        duration.loc[duration.index[k], "minutes"] = 9999
        poisons["minutes"] = duration

        for vector, poisoned in poisons.items():
            result = build_features(poisoned)
            pd.testing.assert_frame_equal(
                baseline.loc[:k, features],
                result.loc[:k, features],
                check_exact=False,
                atol=1e-9,
                obj=f"envenenando '{vector}' en el partido {k}",
            )


@pytest.mark.critical
def test_poisoning_does_affect_later_matches():
    """Control positivo: si nada cambiase nunca, el test anterior seria vacuo."""
    facts = _facts([("Clay", "A", "B")] * 8)
    baseline = build_features(facts)

    poisoned = facts.copy()
    poisoned.loc[poisoned.index[0], "target"] = 1 - poisoned.loc[poisoned.index[0], "target"]
    result = build_features(poisoned)

    # Se mira el head-to-head, que acumula desde el primer enfrentamiento: la
    # forma reciente con ventana 5 ya no alcanza al partido 0 en la fila 6.
    later = baseline.loc[1:, "head_to_head_before_match"].to_numpy()
    assert not np.allclose(later, result.loc[1:, "head_to_head_before_match"].to_numpy())


@pytest.mark.critical
def test_first_match_of_a_player_has_no_history_features():
    """Nadie llega a su primer partido con forma, rachas o estadisticas previas."""
    facts = _facts([("Clay", "A", "B")])
    result = build_features(facts)

    for name in FEATURE_NAMES:
        if name in {"head_to_head_before_match"}:
            continue
        if name.startswith(("matches_last", "minutes_played")):
            assert result.loc[0, name] == 0.0, name
            continue
        if name in {"ranking_diff", "rank_points_diff", "age_diff", "height_diff"}:
            continue  # vienen del propio parte del partido, conocidos de antemano
        assert pd.isna(result.loc[0, name]), f"{name} deberia ser nulo en el primer partido"

    assert result.loc[0, "head_to_head_before_match"] == 0.0


@pytest.mark.critical
def test_output_contains_no_outcome_columns():
    """La tabla de features no puede arrastrar el resultado del partido."""
    result = build_features(_facts([("Clay", "A", "B"), ("Clay", "B", "A")]))
    leaked = set(result.columns) & schema.FORBIDDEN_AS_FEATURE
    assert not leaked, f"La tabla de features expone columnas prohibidas: {leaked}"
    assert "target" not in result.columns


@pytest.mark.critical
def test_generation_requires_chronological_order():
    facts = _facts([("Clay", "A", "B"), ("Clay", "B", "C"), ("Hard", "C", "A")])
    assert len(build_features(facts)) == 3

    with pytest.raises(ValueError, match="orden cronologico"):
        build_features(facts.iloc[::-1])


# =============================================================================
# Antisimetria
# =============================================================================


@pytest.mark.critical
def test_features_antisymmetry():
    """Intercambiar A y B debe invertir el signo de todas las diferencias."""
    facts = _facts(
        [
            ("Clay", "A", "B"),
            ("Hard", "B", "C"),
            ("Clay", "C", "A"),
            ("Grass", "A", "C"),
            ("Clay", "B", "A"),
            ("Hard", "A", "B"),
        ]
    )
    direct = build_features(facts)
    mirrored = build_features(_mirror(facts))

    for name in FEATURE_NAMES:
        left = direct[name].to_numpy(dtype=float)
        right = mirrored[name].to_numpy(dtype=float)
        assert np.allclose(left, -right, equal_nan=True), f"{name} no es antisimetrica"


def test_context_columns_are_not_mirrored():
    """La superficie o la ronda no dependen de quien sea A."""
    facts = _facts([("Clay", "A", "B"), ("Hard", "B", "A")])
    direct = build_features(facts)
    mirrored = build_features(_mirror(facts))
    for column in CONTEXT_COLUMNS:
        pd.testing.assert_series_equal(direct[column], mirrored[column])


# =============================================================================
# Correccion de los calculos
# =============================================================================


def test_match_statistics_against_manual_calculation():
    player = {
        "svpt": 100,
        "first_in": 60,
        "first_won": 45,
        "second_won": 20,
        "aces": 10,
        "dfs": 5,
        "bp_saved": 3,
        "bp_faced": 4,
    }
    opponent = {
        "svpt": 80,
        "first_in": 50,
        "first_won": 40,
        "second_won": 15,
        "aces": 2,
        "dfs": 8,
        "bp_saved": 2,
        "bp_faced": 6,
    }
    stats = match_statistics(player, opponent)

    assert stats["first_serve_pct"] == pytest.approx(0.60)
    assert stats["first_serve_points_won"] == pytest.approx(45 / 60)
    assert stats["second_serve_points_won"] == pytest.approx(20 / 40)
    assert stats["aces_rate"] == pytest.approx(0.10)
    assert stats["double_fault_rate"] == pytest.approx(0.05)
    assert stats["break_points_saved"] == pytest.approx(0.75)
    # Resto: puntos que el rival no gano con su saque.
    assert stats["return_points_won"] == pytest.approx((80 - 55) / 80)
    # Breaks convertidos: bolas de break del rival que no salvo.
    assert stats["break_points_converted"] == pytest.approx((6 - 2) / 6)


@pytest.mark.parametrize(
    "broken",
    [
        {"svpt": 0},
        {"svpt": np.nan},
        {"bp_faced": 0},
        {"first_in": np.nan},
    ],
)
def test_match_statistics_never_divides_by_zero(broken):
    player = {
        "svpt": 100,
        "first_in": 60,
        "first_won": 45,
        "second_won": 20,
        "aces": 10,
        "dfs": 5,
        "bp_saved": 3,
        "bp_faced": 4,
    } | broken
    stats = match_statistics(player, dict(player))
    assert all(np.isnan(v) or np.isfinite(v) for v in stats.values())


def test_winrate_requires_a_full_window():
    """Con menos partidos que la ventana, la forma reciente no esta definida."""
    history = PlayerHistory()
    for _ in range(4):
        history.record(
            won=True, surface="clay", stats={}, date=pd.Timestamp("2023-01-01"), minutes=90
        )
    assert np.isnan(history.winrate(5))
    history.record(won=False, surface="clay", stats={}, date=pd.Timestamp("2023-01-02"), minutes=90)
    assert history.winrate(5) == pytest.approx(0.8)


def test_winrate_uses_only_the_most_recent_matches():
    history = PlayerHistory()
    for won in [True] * 5 + [False] * 5:
        history.record(
            won=won, surface="clay", stats={}, date=pd.Timestamp("2023-01-01"), minutes=90
        )
    assert history.winrate(5) == pytest.approx(0.0)
    assert history.winrate(10) == pytest.approx(0.5)


def test_surface_winrate_requires_minimum_sample():
    history = PlayerHistory()
    for _ in range(4):
        history.record(
            won=True, surface="clay", stats={}, date=pd.Timestamp("2023-01-01"), minutes=90
        )
    assert np.isnan(history.surface_winrate("clay"))
    history.record(won=False, surface="clay", stats={}, date=pd.Timestamp("2023-01-02"), minutes=90)
    assert history.surface_winrate("clay") == pytest.approx(0.8)
    assert np.isnan(history.surface_winrate("grass"))


def test_workload_windows_respect_the_calendar():
    history = PlayerHistory()
    base = pd.Timestamp("2023-06-01")
    for offset in (0, 3, 6, 10, 20):
        history.record(
            won=True, surface="clay", stats={}, date=base + pd.Timedelta(days=offset), minutes=100
        )
    today = base + pd.Timedelta(days=12)
    # Ventana de 7 dias desde el dia 12: entran los partidos de los dias 6 y 10.
    assert history.matches_in_last_days(today, 7) == 2
    # Ventana de 14 dias: entran los de los dias 0, 3, 6 y 10.
    assert history.matches_in_last_days(today, 14) == 4
    # El partido del dia 20 es posterior y no cuenta en ninguna ventana.
    assert history.matches_in_last_days(today, 14) < 5
    assert history.minutes_in_last_days(today, 7) == pytest.approx(200.0)


def test_days_since_last_match():
    history = PlayerHistory()
    assert np.isnan(history.days_since_last_match(pd.Timestamp("2023-06-01")))
    history.record(won=True, surface="clay", stats={}, date=pd.Timestamp("2023-06-01"), minutes=90)
    assert history.days_since_last_match(pd.Timestamp("2023-06-08")) == pytest.approx(7.0)


def test_head_to_head_accumulates_and_is_signed():
    facts = _facts([("Clay", "A", "B")] * 3 + [("Clay", "B", "A")])
    result = build_features(facts)
    h2h = result["head_to_head_before_match"]

    assert h2h.iloc[0] == 0.0  # primer enfrentamiento
    # Tras los tres primeros partidos, el balance es de 3 para el ganador.
    assert abs(h2h.iloc[3]) == 3.0


def test_head_to_head_is_per_pair():
    facts = _facts([("Clay", "A", "B"), ("Clay", "A", "C"), ("Clay", "A", "B")])
    result = build_features(facts)
    assert result["head_to_head_before_match"].iloc[1] == 0.0  # A vs C es nuevo
    assert abs(result["head_to_head_before_match"].iloc[2]) == 1.0


@pytest.mark.critical
def test_walkovers_do_not_feed_the_history():
    """Un partido no disputado no aporta forma, ni estadisticas, ni carga."""
    facts = _facts([("Clay", "A", "B")] * 6)
    facts.loc[facts.index[0], "completion_status"] = "walkover"
    result = build_features(facts)

    # El segundo partido sigue viendo a ambos sin historial.
    assert result.loc[1, "player_a_matches_before"] == 0
    assert result.loc[1, "player_b_matches_before"] == 0
    assert result.loc[1, "head_to_head_before_match"] == 0.0


def test_statistics_use_a_moving_window_not_the_current_match():
    """La media movil debe ignorar por completo el partido que se esta prediciendo."""
    facts = _facts([("Clay", "A", "B")] * 6)
    # Estadisticas extremas solo en el ultimo partido.
    last = facts.index[-1]
    facts.loc[last, "raw_a_aces"] = 999
    facts.loc[last, "raw_a_svpt"] = 1000

    result = build_features(facts)
    poisoned_free = build_features(_facts([("Clay", "A", "B")] * 6))
    assert result.loc[5, "aces_rate_diff"] == pytest.approx(
        poisoned_free.loc[5, "aces_rate_diff"], nan_ok=True
    )


# =============================================================================
# Estructura y politica de nulos
# =============================================================================


def test_output_columns_are_stable():
    result = build_features(_facts([("Clay", "A", "B")]))
    assert list(result.columns) == list(OUTPUT_COLUMNS)


def test_every_declared_feature_is_produced():
    result = build_features(_facts([("Clay", "A", "B"), ("Clay", "B", "A")]))
    for name in FEATURE_NAMES:
        assert name in result.columns


def test_stat_features_cover_the_brief():
    """Las metricas del brief §7 deben estar todas presentes."""
    required = {
        "first_serve_pct",
        "first_serve_points_won",
        "second_serve_points_won",
        "return_points_won",
        "break_points_saved",
        "break_points_converted",
        "aces_rate",
        "double_fault_rate",
    }
    assert required == set(STAT_NAMES)


def test_nulls_are_preserved_not_imputed():
    """La imputacion es decision del modelo, no de la generacion de features."""
    facts = _facts([("Clay", "A", "B"), ("Clay", "B", "A")])
    result = build_features(facts)
    assert result["winrate_last_20_diff"].isna().all()


def test_missing_ranking_yields_null_not_zero():
    facts = _facts([("Clay", "A", "B")])
    facts.loc[facts.index[0], "player_a_rank"] = pd.NA
    result = build_features(facts)
    assert pd.isna(result.loc[0, "ranking_diff"])


@pytest.mark.critical
def test_rows_without_player_ids_are_emitted_blank_and_ignored():
    """Un partido sin identificador de jugador no puede contaminar ningun historial."""
    facts = _facts([("Clay", "A", "B")] * 4)
    facts.loc[facts.index[1], "player_a_id"] = pd.NA
    result = build_features(facts)

    assert len(result) == 4
    assert pd.isna(result.loc[1, "winrate_last_5_diff"])
    # Toda la fila queda en nulo, incluido el head-to-head: para un partido
    # inutilizable, "desconocido" es la respuesta honesta. Un 0.0 afirmaria que
    # los jugadores no se han enfrentado nunca, que es una afirmacion distinta.
    assert pd.isna(result.loc[1, "head_to_head_before_match"])
    # El partido ignorado no cuenta como experiencia para nadie.
    assert result.loc[2, "player_a_matches_before"] == 1

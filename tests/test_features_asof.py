"""Features «a fecha de hoy» (Carril A, A1): deben coincidir con las del lote.

El calculo de un partido futuro reutiliza la misma funcion de emision que la
pasada por lotes. Estos tests fijan que no divergen y que no hay fuga temporal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tests.test_features_builder import _facts

from edgecourt.features import asof
from edgecourt.features.builder import FEATURE_NAMES, build_features, build_features_with_state
from edgecourt.features.elo import compute_elo

PLAYERS = ["p1", "p2", "p3", "p4", "p5"]


def _matches(n: int = 40) -> pd.DataFrame:
    entries = []
    for i in range(n):
        winner = PLAYERS[i % 5]
        loser = PLAYERS[(i * 2 + 1) % 5]
        if winner == loser:
            loser = PLAYERS[(i + 2) % 5]
        entries.append(("Hard" if i % 3 else "Clay", winner, loser))
    return _facts(entries)


def _attrs(row: pd.Series) -> dict[str, dict[str, float]]:
    return {
        side: {
            "rank": row[f"player_{side}_rank"],
            "rank_points": row[f"player_{side}_rank_points"],
            "age": row[f"player_{side}_age"],
            "height": row[f"player_{side}_height"],
        }
        for side in ("a", "b")
    }


def _surface(row: pd.Series) -> str | None:
    return row["surface"] if isinstance(row["surface"], str) else None


@pytest.mark.critical
def test_asof_matches_batch_for_every_match():
    df = _matches()
    batch = build_features(df)
    elo = compute_elo(df)

    for i in range(4, len(df)):
        row = df.iloc[i]
        state = asof.build_asof_state(df.iloc[:i])
        result = asof.features_for_pair(
            state,
            row["player_a_id"],
            row["player_b_id"],
            surface=_surface(row),
            as_of=pd.Timestamp(row["date"]),
            attributes=_attrs(row),
        )
        for name in asof.ALL_FEATURE_NAMES:
            expected = batch.loc[i, name] if name in batch.columns else elo.loc[i, name]
            assert np.isclose(result.values[name], expected, equal_nan=True), (i, name)
        assert result.player_a_matches_before == batch.loc[i, "player_a_matches_before"]
        assert result.player_b_matches_before == batch.loc[i, "player_b_matches_before"]


def test_build_features_is_unchanged_by_the_state_variant():
    df = _matches()
    table, state = build_features_with_state(df)
    assert table.equals(build_features(df))
    assert set(state.histories) == set(PLAYERS)


@pytest.mark.critical
def test_asof_rejects_a_date_before_the_last_known_match():
    df = _matches()
    state = asof.build_asof_state(df)
    with pytest.raises(ValueError, match="fuga temporal"):
        asof.features_for_pair(
            state, "p1", "p2", surface="hard", as_of=state.data_as_of - pd.Timedelta(days=1)
        )


def test_asof_accepts_the_same_day_as_the_last_match():
    state = asof.build_asof_state(_matches())
    result = asof.features_for_pair(state, "p1", "p2", surface="hard", as_of=state.data_as_of)
    assert result.data_as_of == state.data_as_of


def test_unknown_player_has_no_history_and_does_not_fail():
    state = asof.build_asof_state(_matches())
    result = asof.features_for_pair(
        state, "p1", "desconocido", surface="hard", as_of=state.data_as_of + pd.Timedelta(days=3)
    )
    assert result.player_b_matches_before == 0
    assert result.player_a_matches_before > 0
    assert np.isnan(result.values["winrate_last_5_diff"])
    assert np.isnan(result.values["ranking_diff"])
    # El Elo parte del rating inicial: la diferencia es finita.
    assert np.isfinite(result.values["elo_diff"])


def test_unknown_surface_leaves_surface_features_empty():
    state = asof.build_asof_state(_matches())
    result = asof.features_for_pair(
        state, "p1", "p2", surface=None, as_of=state.data_as_of + pd.Timedelta(days=1)
    )
    assert np.isnan(result.values["surface_elo_diff"])
    assert np.isnan(result.values["surface_winrate_diff"])


def test_latest_attributes_are_used_and_age_advances():
    df = _matches()
    state = asof.build_asof_state(df)
    as_of = state.data_as_of + pd.Timedelta(days=100)
    result = asof.features_for_pair(state, "p1", "p2", surface="hard", as_of=as_of)

    assert result.attribute_source == "latest"
    age_a, age_b = (result.attribute_age_days[k] for k in ("a", "b"))
    assert age_a is not None and age_b is not None and age_a >= 100 and age_b >= 100
    latest_a, latest_b = state.features.latest["p1"], state.features.latest["p2"]
    expected = (float(latest_a["age"]) + age_a / 365.25) - (float(latest_b["age"]) + age_b / 365.25)
    assert result.values["age_diff"] == pytest.approx(expected)


def test_same_player_is_rejected():
    state = asof.build_asof_state(_matches())
    with pytest.raises(ValueError, match="mismo"):
        asof.features_for_pair(state, "p1", "p1", surface="hard", as_of=state.data_as_of)


def test_vector_follows_the_model_order_and_flags_missing_features():
    state = asof.build_asof_state(_matches())
    result = asof.features_for_pair(state, "p1", "p2", surface="hard", as_of=state.data_as_of)
    order = ["elo_diff", FEATURE_NAMES[0]]
    assert result.vector(order) == [result.values["elo_diff"], result.values[FEATURE_NAMES[0]]]
    with pytest.raises(KeyError):
        result.vector(["no_existe"])


def test_features_are_antisymmetric_when_swapping_players():
    state = asof.build_asof_state(_matches())
    as_of = state.data_as_of + pd.Timedelta(days=1)
    ab = asof.features_for_pair(state, "p1", "p2", surface="hard", as_of=as_of)
    ba = asof.features_for_pair(state, "p2", "p1", surface="hard", as_of=as_of)
    for name in ("elo_diff", "winrate_last_5_diff", "head_to_head_before_match"):
        if np.isfinite(ab.values[name]):
            assert ab.values[name] == pytest.approx(-ba.values[name])

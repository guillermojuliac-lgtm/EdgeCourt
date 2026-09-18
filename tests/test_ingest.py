"""Tests de la ingesta: aleatorizacion A/B, orden total y clasificacion de resultados.

Estos tests protegen los riesgos R2, R3 y R9 del IMPLEMENTATION_PLAN.
"""

from __future__ import annotations

import pandas as pd
import pytest
from tests.factories import _raw_frame

from edgecourt.data import ingest, schema

# --- Esquema -----------------------------------------------------------------


def test_output_has_exactly_the_canonical_columns():
    result = ingest.normalise_raw_matches(_raw_frame(), source="test")
    assert list(result.columns) == list(schema.MATCH_FACTS_COLUMNS)


def test_missing_required_source_columns_raise():
    bad = _raw_frame().drop(columns=["winner_id"])
    with pytest.raises(ValueError, match="Faltan columnas"):
        ingest.normalise_raw_matches(bad, source="test")


# --- R3: aleatorizacion A/B --------------------------------------------------


@pytest.mark.critical
def test_ab_randomization_is_balanced():
    """P(target=1) debe ser ~0.5: si no, el modelo aprende el orden de columnas."""
    result = ingest.normalise_raw_matches(_raw_frame(4000), source="test")
    assert result["target"].mean() == pytest.approx(0.5, abs=0.03)


@pytest.mark.critical
def test_ab_randomization_is_deterministic():
    """La misma entrada debe producir siempre el mismo reparto (reproducibilidad)."""
    raw = _raw_frame(200)
    first = ingest.normalise_raw_matches(raw, source="test")
    second = ingest.normalise_raw_matches(raw.copy(), source="test")
    pd.testing.assert_series_equal(first["target"], second["target"])
    pd.testing.assert_series_equal(first["player_a_name"], second["player_a_name"])


@pytest.mark.critical
def test_target_identifies_the_real_winner():
    """El lado marcado por `target` debe ser siempre el ganador real del CSV."""
    raw = _raw_frame(300)
    result = ingest.normalise_raw_matches(raw, source="test").set_index("match_id")

    winners = dict(zip(raw["winner_name"], raw["loser_name"], strict=True))
    for _, row in result.iterrows():
        winner_side = row["player_a_name"] if row["target"] == 1 else row["player_b_name"]
        loser_side = row["player_b_name"] if row["target"] == 1 else row["player_a_name"]
        assert winners[winner_side] == loser_side


@pytest.mark.critical
def test_player_attributes_travel_with_their_player():
    """Al intercambiar lados, todos los atributos deben moverse juntos.

    Un fallo aqui asignaria el ranking de un jugador al otro sin error visible.
    """
    result = ingest.normalise_raw_matches(_raw_frame(100), source="test")
    for _, row in result.iterrows():
        if row["target"] == 1:  # A es el ganador
            assert row["player_a_rank"] == 3 and row["player_a_hand"] == "R"
            assert row["player_b_rank"] == 40 and row["player_b_hand"] == "L"
            assert row["raw_a_aces"] == 5 and row["raw_b_aces"] == 3
        else:
            assert row["player_b_rank"] == 3 and row["player_b_hand"] == "R"
            assert row["player_a_rank"] == 40 and row["player_a_hand"] == "L"
            assert row["raw_b_aces"] == 5 and row["raw_a_aces"] == 3


# --- R2: orden cronologico ---------------------------------------------------


@pytest.mark.critical
def test_output_is_ordered_by_the_total_order_key():
    raw = _raw_frame(50)
    raw["round"] = ["F", "SF", "QF", "R16", "R32"] * 10
    raw["tourney_date"] = ["20230417", "20230410"] * 25
    result = ingest.normalise_raw_matches(raw, source="test")

    keys = result[["date", "tourney_id", "round_order", "match_num"]]
    assert keys.equals(keys.sort_values(list(keys.columns), kind="mergesort"))


@pytest.mark.critical
def test_round_order_respects_tournament_progression():
    """Las rondas deben ordenarse como avanza un torneo, no alfabeticamente."""
    order = schema.ROUND_ORDER
    assert order["R128"] < order["R64"] < order["R32"] < order["R16"] < order["QF"]
    assert order["QF"] < order["SF"] < order["F"]
    assert order["RR"] < order["SF"]  # el round robin precede a las semifinales


def test_unknown_round_gets_order_zero_and_is_flagged():
    raw = _raw_frame(1, round="ZZZ")
    result = ingest.normalise_raw_matches(raw, source="test")
    assert result.loc[0, "round_order"] == 0


# --- Identidad ---------------------------------------------------------------


@pytest.mark.critical
def test_match_id_survives_missing_match_num():
    """Regresion: `match_num` vacio colapsaba cientos de partidos en un id nulo."""
    raw = _raw_frame(20)
    raw["match_num"] = ""
    result = ingest.normalise_raw_matches(raw, source="test")
    assert result["match_id"].notna().all()
    assert result["match_id"].nunique() == 20


def test_match_id_is_stable_across_runs():
    raw = _raw_frame(10)
    first = ingest.normalise_raw_matches(raw, source="test")["match_id"]
    second = ingest.normalise_raw_matches(raw.copy(), source="test")["match_id"]
    assert list(first) == list(second)


def test_identical_matches_share_an_id_and_deduplicate():
    raw = pd.concat([_raw_frame(5), _raw_frame(5)], ignore_index=True)
    result = ingest.normalise_raw_matches(raw, source="test")
    deduped, removed = ingest.deduplicate(result)
    assert removed == 5
    assert len(deduped) == 5


# --- R9: clasificacion de finalizacion ---------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        ("6-4 6-3", "completed"),
        ("7-6(4) 3-6 6-3", "completed"),
        ("3-6 7-6(6) 2-0 RET", "retired"),
        ("2-3 RET", "retired"),
        ("W/O", "walkover"),
        ("6-1 DEF", "defaulted"),
        ("", "unknown"),
    ],
)
def test_completion_status_classification(score, expected):
    result = ingest.normalise_raw_matches(_raw_frame(1, score=score), source="test")
    assert result.loc[0, "completion_status"] == expected


def test_set_counts_follow_the_ab_assignment():
    result = ingest.normalise_raw_matches(_raw_frame(100, score="6-4 3-6 6-2"), source="test")
    winner_sets = result["sets_a"].where(result["target"] == 1, result["sets_b"])
    loser_sets = result["sets_b"].where(result["target"] == 1, result["sets_a"])
    assert (winner_sets == 2).all()
    assert (loser_sets == 1).all()


# --- Normalizacion de vocabularios -------------------------------------------


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("Clay", "clay"), ("HARD", "hard"), ("Grass", "grass"), ("Carpet", "carpet")],
)
def test_surface_normalisation(raw_value, expected):
    result = ingest.normalise_raw_matches(_raw_frame(1, surface=raw_value), source="test")
    assert result.loc[0, "surface"] == expected


def test_unknown_surface_becomes_null_not_garbage():
    result = ingest.normalise_raw_matches(_raw_frame(1, surface="Moqueta"), source="test")
    assert pd.isna(result.loc[0, "surface"])


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("G", "grand_slam"),
        ("M", "masters"),
        ("500", "atp500"),
        ("250", "atp250"),
        ("A", "other"),
        ("D", "davis_cup"),
        ("F", "finals"),
        ("O", "olympics"),
    ],
)
def test_tourney_level_mapping(raw_value, expected):
    result = ingest.normalise_raw_matches(_raw_frame(1, tourney_level=raw_value), source="test")
    assert result.loc[0, "tourney_level"] == expected


@pytest.mark.parametrize(("raw_value", "expected"), [("I", True), ("O", False)])
def test_indoor_normalisation(raw_value, expected):
    result = ingest.normalise_raw_matches(_raw_frame(1, indoor=raw_value), source="test")
    assert bool(result.loc[0, "indoor"]) is expected


def test_missing_indoor_column_is_tolerated():
    """El formato Sackmann original no trae `indoor`: debe quedar nulo, no fallar."""
    raw = _raw_frame(3).drop(columns=["indoor"])
    result = ingest.normalise_raw_matches(raw, source="test")
    assert result["indoor"].isna().all()


def test_dates_are_parsed_from_yyyymmdd():
    result = ingest.normalise_raw_matches(_raw_frame(1), source="test")
    assert result.loc[0, "date"] == pd.Timestamp("2023-04-17")
    assert result.loc[0, "year"] == 2023


def test_dtypes_match_the_schema():
    result = ingest.normalise_raw_matches(_raw_frame(10), source="test")
    for column, expected in schema.DTYPES.items():
        assert str(result[column].dtype) == expected, (
            f"{column}: {result[column].dtype} != {expected}"
        )

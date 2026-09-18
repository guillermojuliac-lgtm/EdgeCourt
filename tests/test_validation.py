"""Tests de las validaciones de integridad del dataset.

Existen porque la fuente de referencia del sector desaparecio y la fuente viva
tiene procedencia mixta: la integridad hay que deducirla de la coherencia interna.
"""

from __future__ import annotations

import pandas as pd
import pytest
from tests.test_ingest import _raw_frame

from edgecourt.data import ingest, schema, validation


@pytest.fixture
def clean() -> pd.DataFrame:
    return ingest.normalise_raw_matches(_raw_frame(50), source="test")


def _codes(report: validation.ValidationReport) -> set[str]:
    return {issue.code for issue in report.issues}


def test_clean_dataset_passes(clean):
    report = validation.validate_match_facts(clean)
    assert report.ok, report.summary()
    assert not report.errors


def test_missing_schema_column_raises(clean):
    with pytest.raises(ValueError, match="Faltan columnas"):
        validation.validate_match_facts(clean.drop(columns=["surface"]))


@pytest.mark.critical
def test_detects_arithmetically_impossible_stats(clean):
    """first_in > svpt es imposible: indica corrupcion de la fuente."""
    df = clean.copy()
    df.loc[0, "raw_a_first_in"] = df.loc[0, "raw_a_svpt"] + 10
    report = validation.validate_match_facts(df)
    assert "stats_first_in" in _codes(report)
    assert not report.ok


@pytest.mark.critical
@pytest.mark.parametrize(
    ("column", "reference", "code"),
    [
        ("raw_a_first_won", "raw_a_first_in", "stats_first_won"),
        ("raw_a_bp_saved", "raw_a_bp_faced", "stats_bp"),
        ("raw_b_aces", "raw_b_svpt", "stats_aces"),
    ],
)
def test_detects_each_arithmetic_violation(clean, column, reference, code):
    df = clean.copy()
    df.loc[0, column] = df.loc[0, reference] + 1
    assert code in _codes(validation.validate_match_facts(df))


@pytest.mark.critical
def test_detects_broken_ab_randomization(clean):
    """Si el target se desequilibra, la aleatorizacion A/B esta rota."""
    df = pd.concat([clean] * 40, ignore_index=True)
    df["match_id"] = [f"m{i}" for i in range(len(df))]
    df["target"] = 1
    report = validation.validate_match_facts(df)
    assert "target_desequilibrado" in _codes(report)
    assert not report.ok


def test_detects_duplicate_match_id(clean):
    df = clean.copy()
    df.loc[1, "match_id"] = df.loc[0, "match_id"]
    assert "match_id_duplicado" in _codes(validation.validate_match_facts(df))


def test_detects_same_player_on_both_sides(clean):
    df = clean.copy()
    df.loc[0, "player_b_id"] = df.loc[0, "player_a_id"]
    assert "mismo_jugador" in _codes(validation.validate_match_facts(df))


def test_detects_incoherent_winner(clean):
    """El ganador debe tener mas sets: si no, target y score se contradicen."""
    df = clean.copy()
    df.loc[0, "target"] = 1
    df.loc[0, "sets_a"] = 0
    df.loc[0, "sets_b"] = 2
    df.loc[0, "completion_status"] = "completed"
    assert "ganador_incoherente" in _codes(validation.validate_match_facts(df))


def test_detects_required_nulls(clean):
    df = clean.copy()
    df.loc[0, "surface"] = pd.NA
    assert "null_obligatorio" in _codes(validation.validate_match_facts(df))


def test_implausible_ranges_are_warnings_not_errors(clean):
    """Un dato raro no invalida la fila: se reporta y se vigila."""
    df = clean.copy()
    df.loc[0, "player_a_age"] = 3.0
    df.loc[1, "minutes"] = 9000
    report = validation.validate_match_facts(df)
    assert "edad_implausible" in _codes(report)
    assert "duracion_implausible" in _codes(report)
    assert report.ok  # siguen siendo avisos


def test_nulls_are_not_treated_as_violations(clean):
    """Una estadistica ausente no es una violacion aritmetica."""
    df = clean.copy()
    df.loc[0, "raw_a_svpt"] = pd.NA
    report = validation.validate_match_facts(df)
    assert "stats_first_in" not in _codes(report)


def test_year_coverage_reports_per_year(clean):
    coverage = validation.year_coverage(clean)
    assert set(coverage.columns) >= {"year", "matches", "pct_stats", "pct_rank"}
    assert coverage["matches"].sum() == len(clean)


# --- Contraste entre fuentes -------------------------------------------------


def test_cross_check_identical_sources_match_fully(clean):
    result = validation.cross_check(clean, clean)
    assert result["match_rate_primary"] == 100.0
    assert result["winner_agreement_pct"] == 100.0


def test_cross_check_is_insensitive_to_ab_side(clean):
    """Cada fuente sortea A/B por su cuenta: el contraste no puede depender del lado."""
    swapped = clean.copy()
    for field in ("id", "name", "rank", "rank_points", "age", "hand", "height"):
        a, b = f"player_a_{field}", f"player_b_{field}"
        swapped[a], swapped[b] = clean[b].copy(), clean[a].copy()
    swapped["target"] = 1 - clean["target"]

    result = validation.cross_check(clean, swapped)
    assert result["match_rate_primary"] == 100.0
    assert result["winner_agreement_pct"] == 100.0


def test_cross_check_detects_disagreement_on_winner(clean):
    flipped = clean.copy()
    flipped["target"] = 1 - flipped["target"]
    result = validation.cross_check(clean, flipped)
    assert result["winner_agreement_pct"] == 0.0


def test_cross_check_handles_no_overlap(clean):
    other = clean.copy()
    other["year"] = 1800
    other["date"] = pd.Timestamp("1800-01-01")
    result = validation.cross_check(clean, other)
    assert result["overlap_years"] == []


def test_cross_check_normalises_accents_and_case():
    normalised = validation._normalise_name(pd.Series(["Novák DJOKOVIĆ", "novak djokovic"]))
    assert normalised.iloc[0] == normalised.iloc[1]


def test_cross_check_empty_input(clean):
    result = validation.cross_check(clean.iloc[:0], clean)
    assert result["matched"] == 0


# --- Barrera anti-leakage (preparacion de PHASE 3) ---------------------------


@pytest.mark.critical
def test_all_match_statistics_are_declared_forbidden():
    """Ninguna estadistica del propio partido puede quedar fuera de la lista negra.

    Si una columna `raw_` se olvidase aqui, `features/` podria consumirla como
    feature directa del partido a predecir: leakage puro.
    """
    for column in schema.RAW_STAT_COLUMNS:
        assert column in schema.FORBIDDEN_AS_FEATURE, f"{column} no esta prohibida"


@pytest.mark.critical
def test_outcome_columns_are_forbidden_as_features():
    for column in ("target", "score", "sets_a", "sets_b", "minutes", "completion_status"):
        assert column in schema.FORBIDDEN_AS_FEATURE


@pytest.mark.critical
def test_raw_prefix_convention_is_respected():
    """Toda columna que describa el partido en curso lleva el prefijo `raw_`."""
    for column in schema.RAW_STAT_COLUMNS:
        assert column.startswith("raw_"), column

"""Tests del pipeline de Elo: persistencia, union y evaluacion."""

from __future__ import annotations

import json

import pytest
from tests.factories import _raw_frame

from edgecourt.data import pipeline as data_pipeline
from edgecourt.data import splits
from edgecourt.features import pipeline as features_pipeline
from edgecourt.storage import read_parquet


@pytest.fixture
def settings_with_matches(settings_factory):
    """Dataset sintetico que cubre varios anos y varios conjuntos temporales."""
    settings = settings_factory()
    settings.ensure_directories()
    tml = settings.raw_dir / "tml"
    tml.mkdir(parents=True, exist_ok=True)

    for year in (2021, 2022, 2023, 2024):
        frame = _raw_frame(80)
        # Un torneo y una fecha distintos por partido: garantiza que cada fila sea
        # un partido real y no una repeticion (el match_id es hash de contenido).
        frame["tourney_id"] = [f"{year}-{i:03d}" for i in range(80)]
        frame["tourney_date"] = [f"{year}{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(80)]
        # Un grupo reducido de jugadores, para que acumulen historial.
        frame["winner_id"] = [f"P{i % 8}" for i in range(80)]
        frame["loser_id"] = [f"P{(i % 8) + 8}" for i in range(80)]
        frame["winner_name"] = [f"Player {chr(97 + i % 8)}" for i in range(80)]
        frame["loser_name"] = [f"Player {chr(105 + i % 8)}" for i in range(80)]
        frame.to_csv(tml / f"{year}.csv", index=False)

    data_pipeline.build_match_facts(settings, min_year=2000)
    return settings


def test_build_elo_persists_partitioned_dataset(settings_with_matches):
    result = features_pipeline.build_elo(settings_with_matches)

    assert result.rows == 320
    assert (result.destination / "year=2023").is_dir()
    stored = read_parquet(result.destination)
    assert len(stored) == 320
    assert "elo_diff" in stored.columns


def test_evaluation_frame_joins_matches_and_ratings(settings_with_matches):
    features_pipeline.build_elo(settings_with_matches)
    frame = features_pipeline.load_evaluation_frame(settings_with_matches, min_matches=0)

    assert {"target", "elo_diff", "surface_elo_diff", "split"} <= set(frame.columns)
    assert frame["split"].isin(["train", "validation", "test", "live"]).all()


def test_min_matches_filters_inexperienced_players(settings_with_matches):
    features_pipeline.build_elo(settings_with_matches)
    everything = features_pipeline.load_evaluation_frame(settings_with_matches, min_matches=0)
    experienced = features_pipeline.load_evaluation_frame(settings_with_matches, min_matches=20)

    assert len(experienced) < len(everything)
    assert (experienced["elo_a_matches_before"] >= 20).all()
    assert (experienced["elo_b_matches_before"] >= 20).all()


def test_walkovers_are_excluded_from_evaluation(settings_with_matches):
    features_pipeline.build_elo(settings_with_matches)
    frame = features_pipeline.load_evaluation_frame(settings_with_matches, min_matches=0)
    assert (frame["completion_status"] != "walkover").all()


@pytest.mark.critical
def test_evaluation_compares_against_the_coin_flip(settings_with_matches):
    """Toda evaluacion debe incluir la referencia trivial, para situar los numeros."""
    features_pipeline.build_elo(settings_with_matches)
    frame = features_pipeline.load_evaluation_frame(settings_with_matches, min_matches=0)
    table, calibrations = features_pipeline.evaluate_split(frame, "validation")

    assert "moneda" in set(table["model"])
    assert set(features_pipeline.ELO_VARIANTS) <= set(table["model"])
    coin = table[table["model"] == "moneda"].iloc[0]
    assert coin["brier"] == pytest.approx(0.25, abs=1e-6)
    assert set(calibrations) == set(features_pipeline.ELO_VARIANTS)


def test_evaluating_an_empty_split_fails_loudly(settings_with_matches):
    features_pipeline.build_elo(settings_with_matches)
    frame = features_pipeline.load_evaluation_frame(settings_with_matches, min_matches=0)
    with pytest.raises(ValueError, match="no contiene partidos"):
        features_pipeline.evaluate_split(frame, "live")


@pytest.mark.critical
def test_evaluating_test_split_is_recorded(settings_with_matches):
    """Consultar TEST debe dejar rastro siempre (defensa contra leakage humano)."""
    settings = settings_with_matches
    features_pipeline.build_elo(settings)

    assert splits.count_test_evaluations(settings.results_dir) == 0
    features_pipeline.evaluate_elo(settings, split_names=("test",), min_matches=0)
    assert splits.count_test_evaluations(settings.results_dir) == 1

    features_pipeline.evaluate_elo(settings, split_names=("test",), min_matches=0)
    assert splits.count_test_evaluations(settings.results_dir) == 2


def test_evaluating_validation_is_not_recorded(settings_with_matches):
    """VALIDATION es el conjunto de desarrollo: se puede consultar libremente."""
    settings = settings_with_matches
    features_pipeline.build_elo(settings)
    features_pipeline.evaluate_elo(settings, split_names=("validation",), min_matches=0)
    assert splits.count_test_evaluations(settings.results_dir) == 0


def test_evaluation_writes_a_report(settings_with_matches):
    settings = settings_with_matches
    features_pipeline.build_elo(settings)
    features_pipeline.evaluate_elo(settings, split_names=("validation",), min_matches=0)

    report = json.loads((settings.results_dir / "elo_benchmark.json").read_text())
    assert "validation" in report["splits"]
    assert report["splits"]["validation"]["metrics"]
    assert report["splits"]["validation"]["calibration"]


@pytest.mark.critical
def test_elo_dataset_contains_no_outcome_columns(settings_with_matches):
    """El dataset de Elo no puede arrastrar el resultado del partido.

    Se une por `match_id` con `match_facts` cuando hace falta; si trajera el
    target o las estadisticas del partido, seria una via de leakage abierta.
    """
    from edgecourt.data import schema

    result = features_pipeline.build_elo(settings_with_matches)
    stored = read_parquet(result.destination)
    forbidden = set(stored.columns) & schema.FORBIDDEN_AS_FEATURE
    assert not forbidden, f"El dataset de Elo expone columnas prohibidas: {forbidden}"

"""Tests de la disciplina temporal del entrenamiento.

Lo que se protege aqui no es que el codigo corra, sino que TEST no contamine
ninguna decision. Un fallo en estos tests invalida cualquier resultado.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from edgecourt.data import splits
from edgecourt.models.logistic import DEFAULT_FEATURES
from edgecourt.training import pipeline as training


@pytest.fixture
def frame() -> pd.DataFrame:
    """Frame sintetico con los cuatro conjuntos temporales."""
    rng = np.random.default_rng(11)
    rows = []
    for year, n in ((2020, 2000), (2023, 600), (2024, 600), (2026, 200)):
        data = {name: rng.normal(0, 1, n) for name in DEFAULT_FEATURES}
        logit = 1.1 * data["elo_diff"] + 0.5 * data["surface_elo_diff"]
        probability = 1.0 / (1.0 + np.exp(-logit))
        block = pd.DataFrame(data)
        block["year"] = year
        block["target"] = (rng.uniform(size=n) < probability).astype(int)
        block["match_id"] = [f"{year}-{i}" for i in range(n)]
        rows.append(block)
    combined = pd.concat(rows, ignore_index=True)
    combined["split"] = splits.assign_split(combined["year"])
    return combined


# --- Disciplina temporal ------------------------------------------------------


@pytest.mark.critical
def test_hyperparameter_search_never_touches_test(frame, monkeypatch):
    """La busqueda de C solo puede ver TRAIN y VALIDATION.

    Se envenena TEST con datos absurdos: si la busqueda los mirase, el resultado
    cambiaria. Debe ser identico.
    """
    clean = training.search_hyperparameters(frame, c_grid=(0.01, 1.0))

    poisoned = frame.copy()
    test_rows = poisoned["split"] == "test"
    for name in DEFAULT_FEATURES:
        poisoned.loc[test_rows, name] = 999.0
    poisoned.loc[test_rows, "target"] = 1

    after = training.search_hyperparameters(poisoned, c_grid=(0.01, 1.0))
    pd.testing.assert_frame_equal(clean, after)


@pytest.mark.critical
def test_training_never_touches_validation_or_test(frame):
    """El ajuste del modelo solo puede usar TRAIN.

    Se envenenan VALIDATION y TEST: los coeficientes aprendidos no pueden cambiar.
    """
    from edgecourt.models.logistic import LogisticModel

    train = frame[frame["split"] == "train"]
    baseline = LogisticModel().fit(train, train["target"]).coefficients()

    poisoned = frame.copy()
    other = poisoned["split"].isin(["validation", "test", "live"])
    for name in DEFAULT_FEATURES:
        poisoned.loc[other, name] = -999.0
    poisoned.loc[other, "target"] = 0

    train_again = poisoned[poisoned["split"] == "train"]
    after = LogisticModel().fit(train_again, train_again["target"]).coefficients()
    pd.testing.assert_frame_equal(baseline, after)


@pytest.mark.critical
def test_search_selects_by_log_loss_not_accuracy(frame):
    """Se optimiza la calidad de la probabilidad, no cuantas veces se acierta."""
    search = training.search_hyperparameters(frame, c_grid=(0.001, 0.1, 1.0))
    assert search["log_loss"].is_monotonic_increasing, "debe venir ordenada por log loss"
    assert search.iloc[0]["log_loss"] == search["log_loss"].min()


def test_search_requires_both_splits(frame):
    only_train = frame[frame["split"] == "train"]
    with pytest.raises(ValueError, match="TRAIN y de VALIDATION"):
        training.search_hyperparameters(only_train, c_grid=(1.0,))


def test_search_reports_every_candidate(frame):
    grid = (0.01, 0.1, 1.0)
    search = training.search_hyperparameters(frame, c_grid=grid)
    assert set(search["C"]) == set(grid)
    assert len(search) == len(grid)


# --- Entrenamiento completo ---------------------------------------------------


@pytest.fixture
def settings_with_datasets(settings_factory, frame, monkeypatch):
    """Escribe los datasets que el pipeline espera encontrar."""
    from edgecourt.storage import write_partitioned_parquet

    settings = settings_factory()
    settings.ensure_directories()

    features = frame.drop(columns=["target", "split"]).copy()
    write_partitioned_parquet(
        features, settings.processed_dir / "features", partition_cols=["year"]
    )

    outcomes = frame[["match_id", "year", "target"]].copy()
    outcomes["completion_status"] = "completed"
    outcomes["surface"] = "hard"
    outcomes["tourney_level"] = "atp250"
    write_partitioned_parquet(outcomes, settings.processed_dir / "matches", partition_cols=["year"])

    elo = frame[["match_id", "year"]].copy()
    elo["elo_a_matches_before"] = 50
    elo["elo_b_matches_before"] = 50
    write_partitioned_parquet(elo, settings.processed_dir / "elo", partition_cols=["year"])

    return settings


def test_training_saves_to_the_challenger_slot(settings_with_datasets):
    """Nada entra en produccion automaticamente (brief §18)."""
    result = training.train_logistic(settings_with_datasets, c_grid=(0.1, 1.0), min_matches=0)

    from edgecourt.models.registry import list_models

    assert len(list_models(settings_with_datasets.challenger_dir)) == 1
    assert list_models(settings_with_datasets.production_dir) == []
    assert result.model_id.startswith("tennis-logreg-")


@pytest.mark.critical
def test_manifest_records_the_search_budget(settings_with_datasets):
    """Cuanto se busco es parte de la procedencia: una rejilla grande infla el optimismo."""
    grid = (0.01, 0.1, 1.0)
    result = training.train_logistic(settings_with_datasets, c_grid=grid, min_matches=0)

    assert result.manifest.hyperparameters["search_budget"] == len(grid)
    assert result.manifest.hyperparameters["search_grid"] == list(grid)
    assert result.manifest.train_last_year == 2022 or result.manifest.train_last_year == 2020


def test_training_only_uses_train_years(settings_with_datasets):
    result = training.train_logistic(settings_with_datasets, c_grid=(1.0,), min_matches=0)
    assert result.n_train == 2000, "solo las filas de TRAIN"
    assert result.manifest.train_first_year == 2020


# --- Comparacion contra los benchmarks ---------------------------------------


@pytest.mark.critical
def test_comparison_uses_identical_rows_for_every_model(settings_with_datasets):
    """Todos los modelos deben evaluarse sobre exactamente las mismas filas."""
    result = training.train_logistic(settings_with_datasets, c_grid=(1.0,), min_matches=0)
    comparison = training.compare_against_elo(
        settings_with_datasets, result.model, min_matches=0, model_id=result.model_id
    )

    for split_name in ("validation", "test"):
        rows = comparison["splits"][split_name]["metrics"]
        counts = {row["n"] for row in rows}
        assert len(counts) == 1, f"'{split_name}' compara sobre muestras distintas: {counts}"
        assert comparison["splits"][split_name]["n_matches"] == counts.pop()


@pytest.mark.critical
def test_comparison_includes_the_elo_benchmarks_and_the_coin(settings_with_datasets):
    result = training.train_logistic(settings_with_datasets, c_grid=(1.0,), min_matches=0)
    comparison = training.compare_against_elo(
        settings_with_datasets, result.model, min_matches=0, model_id=result.model_id
    )

    names = {row["model"] for row in comparison["splits"]["validation"]["metrics"]}
    assert {"logistic_regression", "elo_global", "elo_surface", "elo_blend_50", "moneda"} == names


@pytest.mark.critical
def test_evaluating_test_is_recorded(settings_with_datasets):
    """Cada consulta a TEST deja rastro."""
    result = training.train_logistic(settings_with_datasets, c_grid=(1.0,), min_matches=0)
    before = splits.count_test_evaluations(settings_with_datasets.results_dir)

    training.compare_against_elo(
        settings_with_datasets,
        result.model,
        split_names=("test",),
        min_matches=0,
        model_id=result.model_id,
    )
    assert splits.count_test_evaluations(settings_with_datasets.results_dir) == before + 1


def test_comparison_reports_calibration_per_model(settings_with_datasets):
    result = training.train_logistic(settings_with_datasets, c_grid=(1.0,), min_matches=0)
    comparison = training.compare_against_elo(
        settings_with_datasets, result.model, min_matches=0, model_id=result.model_id
    )

    calibration = comparison["splits"]["validation"]["calibration"]
    assert "logistic_regression" in calibration
    assert "elo_global" in calibration
    assert calibration["logistic_regression"], "la tabla de calibracion no puede venir vacia"


def test_comparison_writes_a_report(settings_with_datasets):
    result = training.train_logistic(settings_with_datasets, c_grid=(1.0,), min_matches=0)
    training.compare_against_elo(
        settings_with_datasets, result.model, min_matches=0, model_id=result.model_id
    )
    report = json.loads(
        (settings_with_datasets.results_dir / "logistic_benchmark.json").read_text()
    )
    assert report["model_id"] == result.model_id
    assert "validation" in report["splits"]

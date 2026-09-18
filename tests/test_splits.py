"""Tests de la division temporal."""

from __future__ import annotations

import pandas as pd
import pytest

from edgecourt.data import splits


def test_splits_do_not_overlap():
    """Un ano no puede pertenecer a dos conjuntos: seria leakage directo."""
    seen: dict[int, str] = {}
    for name, (first, last) in splits.SPLITS.items():
        for year in range(first, min(last, 2100) + 1):
            assert year not in seen, f"{year} esta en '{name}' y en '{seen[year]}'"
            seen[year] = name


@pytest.mark.critical
def test_splits_are_chronologically_ordered():
    """TRAIN debe preceder a VALIDATION, que precede a TEST, que precede a LIVE."""
    order = ["train", "validation", "test", "live"]
    last_end = -1
    for name in order:
        split = splits.get_split(name)
        assert split.first_year > last_end, f"'{name}' empieza antes de terminar el anterior"
        last_end = split.last_year


def test_assign_split_labels_each_year():
    result = splits.assign_split(pd.Series([1999, 2000, 2022, 2023, 2024, 2025, 2026, 2030]))
    assert list(result) == [pd.NA, "train", "train", "validation", "test", "test", "live", "live"]


def test_filter_split_selects_only_its_years():
    df = pd.DataFrame({"year": [2022, 2023, 2024], "x": [1, 2, 3]})
    assert list(splits.filter_split(df, "validation")["x"]) == [2]
    assert list(splits.filter_split(df, "test")["x"]) == [3]


def test_unknown_split_is_rejected():
    with pytest.raises(ValueError, match="Split desconocido"):
        splits.get_split("todo")


@pytest.mark.critical
def test_test_evaluations_are_recorded(tmp_path):
    """Cada consulta al TEST debe dejar rastro: es la defensa contra el leakage humano."""
    assert splits.count_test_evaluations(tmp_path) == 0

    splits.record_test_evaluation(
        tmp_path, model="elo-v1", reason="benchmark", metrics={"brier": 0.2}
    )
    splits.record_test_evaluation(tmp_path, model="elo-v2", reason="otra", metrics={"brier": 0.19})

    assert splits.count_test_evaluations(tmp_path) == 2
    content = (tmp_path / splits.EVALUATION_LOG).read_text()
    assert "elo-v1" in content and "elo-v2" in content


def test_evaluation_log_is_append_only(tmp_path):
    """Registrar una evaluacion nunca puede borrar las anteriores."""
    splits.record_test_evaluation(tmp_path, model="a", reason="r", metrics={})
    first = (tmp_path / splits.EVALUATION_LOG).read_text()
    splits.record_test_evaluation(tmp_path, model="b", reason="r", metrics={})
    second = (tmp_path / splits.EVALUATION_LOG).read_text()
    assert second.startswith(first)

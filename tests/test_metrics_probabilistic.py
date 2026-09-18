"""Tests de las metricas probabilisticas, contra valores calculados a mano."""

from __future__ import annotations

import numpy as np
import pytest

from edgecourt.metrics import probabilistic as m


def test_brier_of_a_perfect_model_is_zero():
    assert m.brier_score([1, 0, 1], [1.0, 0.0, 1.0]) == 0.0


def test_brier_of_the_coin_flip_is_a_quarter():
    assert m.brier_score([1, 0, 1, 0], [0.5] * 4) == pytest.approx(0.25)


def test_brier_matches_manual_calculation():
    # ((0.8-1)^2 + (0.3-0)^2) / 2 = (0.04 + 0.09) / 2
    assert m.brier_score([1, 0], [0.8, 0.3]) == pytest.approx(0.065)


def test_log_loss_of_the_coin_flip_is_ln2():
    assert m.log_loss([1, 0], [0.5, 0.5]) == pytest.approx(np.log(2))


def test_log_loss_is_finite_for_certain_and_wrong():
    """Una prediccion de 0 que resulta ser 1 no puede dar infinito."""
    assert np.isfinite(m.log_loss([1], [0.0]))


def test_accuracy_counts_hits_at_the_threshold():
    assert m.accuracy([1, 0, 1, 0], [0.9, 0.1, 0.4, 0.6]) == pytest.approx(0.5)


def test_roc_auc_perfect_and_inverted():
    assert m.roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)
    assert m.roc_auc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(0.0)


def test_roc_auc_of_constant_predictions_is_one_half():
    """Con todo empatado, la ordenacion no aporta nada."""
    assert m.roc_auc([1, 0, 1, 0], [0.5] * 4) == pytest.approx(0.5)


def test_roc_auc_handles_ties_like_scipy_rankdata():
    assert m.roc_auc([1, 0, 1, 0], [0.9, 0.9, 0.1, 0.1]) == pytest.approx(0.5)


def test_roc_auc_is_nan_without_both_classes():
    assert np.isnan(m.roc_auc([1, 1, 1], [0.2, 0.5, 0.9]))


def test_brier_skill_score_sign():
    y = [1, 0, 1, 0]
    good = [0.9, 0.1, 0.9, 0.1]
    coin = [0.5] * 4
    assert m.brier_skill_score(y, good, coin) > 0
    assert m.brier_skill_score(y, coin, good) < 0
    assert m.brier_skill_score(y, coin, coin) == pytest.approx(0.0)


# --- Calibracion -------------------------------------------------------------


def test_calibration_table_of_a_perfectly_calibrated_model():
    rng = np.random.default_rng(42)
    p = rng.uniform(0.05, 0.95, 40000)
    y = (rng.uniform(size=40000) < p).astype(int)

    table = m.calibration_table(y, p)
    assert (table["gap"].abs() < 0.02).all(), table
    assert m.expected_calibration_error(y, p) < 0.01


def test_calibration_table_detects_overconfidence():
    """Un modelo que dice 0.9 y acierta el 60% debe mostrar un gap negativo."""
    y = np.array([1] * 60 + [0] * 40)
    p = np.full(100, 0.9)
    table = m.calibration_table(y, p)
    row = table[table["bucket"] == "[0.9, 1.0)"].iloc[0]
    assert row["n"] == 100
    assert row["gap"] == pytest.approx(-0.3, abs=0.01)
    assert m.expected_calibration_error(y, p) == pytest.approx(0.3, abs=0.01)


def test_calibration_table_reports_bucket_sizes():
    """El tamano de cada bucket es imprescindible para interpretarlo."""
    p = np.concatenate([np.full(900, 0.15), np.full(100, 0.85)])
    y = np.concatenate([np.zeros(900), np.ones(100)]).astype(int)
    table = m.calibration_table(y, p)
    assert set(table.columns) == {
        "bucket",
        "n",
        "pct_of_total",
        "mean_predicted",
        "observed_freq",
        "gap",
    }
    assert table["n"].sum() == 1000
    assert table["pct_of_total"].sum() == pytest.approx(100.0, abs=0.1)


def test_probability_of_one_is_included_in_the_last_bucket():
    table = m.calibration_table([1, 1], [1.0, 1.0])
    assert table["n"].sum() == 2


def test_empty_buckets_are_omitted():
    table = m.calibration_table([1, 0], [0.95, 0.92])
    assert len(table) == 1


# --- Validacion de entradas ---------------------------------------------------


def test_mismatched_lengths_are_rejected():
    with pytest.raises(ValueError, match="Dimensiones"):
        m.brier_score([1, 0], [0.5])


def test_non_binary_targets_are_rejected():
    with pytest.raises(ValueError, match="solo 0 y 1"):
        m.brier_score([1, 2], [0.5, 0.5])


def test_empty_input_is_rejected():
    with pytest.raises(ValueError, match="No hay observaciones"):
        m.brier_score([], [])


def test_nan_rows_are_dropped():
    assert m.brier_score([1, 0, np.nan], [0.8, 0.3, 0.5]) == pytest.approx(0.065)


def test_evaluate_returns_the_full_report():
    report = m.evaluate([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], "prueba")
    row = report.as_row()
    assert row["model"] == "prueba"
    assert row["n"] == 4
    assert 0.0 <= row["brier"] <= 1.0
    assert set(row) == {"model", "n", "brier", "log_loss", "accuracy", "roc_auc", "ece"}

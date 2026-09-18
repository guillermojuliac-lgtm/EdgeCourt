"""Tests de la regresion logistica y del contrato de modelo."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from edgecourt.models.base import (
    PROBABILITY_A,
    PROBABILITY_B,
    as_probability_frame,
    feature_hash,
    validate_probabilities,
)
from edgecourt.models.logistic import DEFAULT_FEATURES, LogisticConfig, LogisticModel


@pytest.fixture
def training_data() -> tuple[pd.DataFrame, pd.Series]:
    """Datos sinteticos con senal real y estructura antisimetrica."""
    rng = np.random.default_rng(7)
    n = 3000
    features = pd.DataFrame({name: rng.normal(0, 1, n) for name in DEFAULT_FEATURES})
    # El resultado depende de verdad de dos features, con ruido.
    logit = 1.2 * features["elo_diff"] + 0.6 * features["surface_elo_diff"]
    probability = 1.0 / (1.0 + np.exp(-logit))
    target = pd.Series((rng.uniform(size=n) < probability).astype(int))
    return features, target


# --- Contrato de probabilidades ----------------------------------------------


@pytest.mark.critical
def test_probability_sum(training_data):
    """P(A) + P(B) = 1 (brief §9)."""
    features, target = training_data
    model = LogisticModel().fit(features, target)
    predictions = model.predict_proba(features)

    total = predictions[PROBABILITY_A] + predictions[PROBABILITY_B]
    assert np.allclose(total, 1.0, atol=1e-12)
    assert predictions[PROBABILITY_A].between(0, 1).all()


@pytest.mark.critical
def test_validate_probabilities_rejects_broken_output():
    valid = pd.DataFrame({PROBABILITY_A: [0.6], PROBABILITY_B: [0.4]})
    assert validate_probabilities(valid) is valid

    with pytest.raises(ValueError, match="!= 1"):
        validate_probabilities(pd.DataFrame({PROBABILITY_A: [0.6], PROBABILITY_B: [0.6]}))
    with pytest.raises(ValueError, match="fuera de"):
        validate_probabilities(pd.DataFrame({PROBABILITY_A: [1.4], PROBABILITY_B: [-0.4]}))
    with pytest.raises(ValueError, match="nulos"):
        validate_probabilities(pd.DataFrame({PROBABILITY_A: [np.nan], PROBABILITY_B: [0.4]}))
    with pytest.raises(ValueError, match="Faltan columnas"):
        validate_probabilities(pd.DataFrame({PROBABILITY_A: [0.6]}))


def test_as_probability_frame_builds_the_pair():
    frame = as_probability_frame([0.7, 0.2], index=pd.Index([10, 11]))
    assert list(frame[PROBABILITY_B]) == [pytest.approx(0.3), pytest.approx(0.8)]
    assert list(frame.index) == [10, 11]


# --- Antisimetria -------------------------------------------------------------


@pytest.mark.critical
def test_model_is_antisymmetric_by_construction(training_data):
    """Invertir los lados debe invertir exactamente la prediccion.

    `P(A | -X) = 1 - P(A | X)`. Se cumple porque el modelo no tiene intercepto,
    imputa a cero y escala sin centrar. Si alguien anadiese un intercepto o
    cambiase la imputacion a la mediana, este test lo detectaria.
    """
    features, target = training_data
    model = LogisticModel().fit(features, target)

    direct = model.predict_proba(features)[PROBABILITY_A]
    mirrored = model.predict_proba(-features)[PROBABILITY_A]

    assert np.allclose(direct, 1.0 - mirrored, atol=1e-12)


@pytest.mark.critical
def test_model_has_no_intercept(training_data):
    """Un intercepto significaria 'el lado A gana mas', que es falso por diseno."""
    features, target = training_data
    model = LogisticModel().fit(features, target)
    estimator = model.pipeline.named_steps["model"]

    assert estimator.fit_intercept is False
    assert np.allclose(estimator.intercept_, 0.0)


@pytest.mark.critical
def test_all_zero_features_give_an_even_match(training_data):
    """Sin ninguna diferencia conocida, la prediccion debe ser exactamente 0,5."""
    features, target = training_data
    model = LogisticModel().fit(features, target)

    neutral = pd.DataFrame({name: [0.0] for name in DEFAULT_FEATURES})
    assert model.predict_proba(neutral)[PROBABILITY_A].iloc[0] == pytest.approx(0.5)


@pytest.mark.critical
def test_missing_features_are_imputed_as_no_difference(training_data):
    """Un nulo significa 'no se sabe', y eso es una diferencia de cero."""
    features, target = training_data
    model = LogisticModel().fit(features, target)

    all_null = pd.DataFrame({name: [np.nan] for name in DEFAULT_FEATURES})
    assert model.predict_proba(all_null)[PROBABILITY_A].iloc[0] == pytest.approx(0.5)


# --- Aprendizaje --------------------------------------------------------------


def test_model_learns_the_signal(training_data):
    features, target = training_data
    model = LogisticModel().fit(features, target)
    coefficients = model.coefficients()

    top = coefficients.iloc[0]["feature"]
    assert top == "elo_diff", f"la feature dominante deberia ser elo_diff, fue {top}"
    weights = dict(zip(coefficients["feature"], coefficients["coefficient_scaled"], strict=True))
    assert weights["elo_diff"] > weights["surface_elo_diff"] > 0


def test_stronger_regularisation_shrinks_coefficients(training_data):
    features, target = training_data
    loose = LogisticModel(config=LogisticConfig(C=10.0)).fit(features, target)
    tight = LogisticModel(config=LogisticConfig(C=0.001)).fit(features, target)

    assert (
        tight.coefficients()["coefficient_scaled"].abs().sum()
        < loose.coefficients()["coefficient_scaled"].abs().sum()
    )


def test_predicting_before_training_fails():
    with pytest.raises(RuntimeError, match="no esta entrenado"):
        LogisticModel().predict_proba(pd.DataFrame({name: [0.0] for name in DEFAULT_FEATURES}))


def test_missing_feature_column_is_rejected(training_data):
    features, target = training_data
    model = LogisticModel().fit(features, target)
    with pytest.raises(ValueError, match="Faltan features"):
        model.predict_proba(features.drop(columns=["elo_diff"]))


def test_extra_columns_are_ignored(training_data):
    """El frame de evaluacion trae columnas de mas: no deben estorbar."""
    features, target = training_data
    model = LogisticModel().fit(features, target)
    with_extras = features.assign(target=target, match_id="x", surface="clay")
    assert len(model.predict_proba(with_extras)) == len(features)


# --- Features del modelo ------------------------------------------------------


@pytest.mark.critical
def test_model_never_sees_odds_or_outcome():
    """El contrato prohibe que un modelo conozca precios o resultados."""
    from edgecourt.data import schema

    forbidden = schema.FORBIDDEN_AS_FEATURE | {
        "back_price",
        "lay_price",
        "market_probability",
        "odds",
        "stake",
        "bankroll",
    }
    assert not (set(DEFAULT_FEATURES) & forbidden)


def test_context_columns_are_excluded():
    """Una variable que no distingue A de B no puede informar sobre quien gana."""
    from edgecourt.features.builder import CONTEXT_COLUMNS

    assert not (set(DEFAULT_FEATURES) & set(CONTEXT_COLUMNS))


def test_feature_hash_detects_changes():
    base = feature_hash(["a", "b", "c"])
    assert base == feature_hash(["a", "b", "c"])
    assert base != feature_hash(["a", "c", "b"]), "el orden importa"
    assert base != feature_hash(["a", "b"])

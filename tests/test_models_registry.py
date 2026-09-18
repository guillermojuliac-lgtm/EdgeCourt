"""Tests del versionado de modelos (brief §23: test_model_versioning)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from edgecourt.models.base import ModelManifest, new_model_id
from edgecourt.models.logistic import DEFAULT_FEATURES, LogisticModel
from edgecourt.models.registry import (
    ModelIntegrityError,
    latest_model,
    list_models,
    load_model,
    save_model,
)


@pytest.fixture
def trained_model() -> LogisticModel:
    rng = np.random.default_rng(3)
    features = pd.DataFrame({name: rng.normal(0, 1, 400) for name in DEFAULT_FEATURES})
    target = pd.Series((rng.uniform(size=400) < 0.5).astype(int))
    return LogisticModel().fit(features, target)


def _save(model, directory, **overrides):
    defaults = {
        "model_type": "logreg",
        "train_first_year": 2000,
        "train_last_year": 2022,
        "n_train_rows": 400,
        "feature_names": list(DEFAULT_FEATURES),
        "hyperparameters": {"C": 1.0},
    }
    return save_model(model, directory, **(defaults | overrides))


@pytest.mark.critical
def test_model_versioning_records_full_provenance(trained_model, tmp_path):
    """Un modelo debe ser atribuible: que datos vio, con que features y que hiperparametros."""
    _, manifest = _save(trained_model, tmp_path)

    assert manifest.model_id.startswith("tennis-logreg-")
    assert manifest.train_first_year == 2000
    assert manifest.train_last_year == 2022
    assert manifest.n_train_rows == 400
    assert manifest.feature_names == list(DEFAULT_FEATURES)
    assert manifest.feature_hash
    assert manifest.hyperparameters["C"] == 1.0
    assert len(manifest.artifact_sha256) == 64
    assert manifest.trained_at


@pytest.mark.critical
def test_saved_model_round_trips(trained_model, tmp_path):
    _, manifest = _save(trained_model, tmp_path)
    loaded, loaded_manifest = load_model(tmp_path, manifest.model_id)

    features = pd.DataFrame({name: [0.5] for name in DEFAULT_FEATURES})
    assert loaded.predict_proba(features).equals(trained_model.predict_proba(features))
    assert loaded_manifest.model_id == manifest.model_id


@pytest.mark.critical
def test_tampered_artifact_is_detected(trained_model, tmp_path):
    """Un binario sustituido no puede pasar por el modelo original."""
    artifact, manifest = _save(trained_model, tmp_path)
    artifact.write_bytes(artifact.read_bytes() + b"basura")

    with pytest.raises(ModelIntegrityError, match="no coincide"):
        load_model(tmp_path, manifest.model_id)


def test_missing_files_fail_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="manifiesto"):
        load_model(tmp_path, "no-existe")


def test_model_ids_are_unique_and_sortable():
    from datetime import UTC, datetime

    early = new_model_id("logreg", trained_at=datetime(2026, 1, 1, tzinfo=UTC))
    late = new_model_id("logreg", trained_at=datetime(2026, 6, 1, tzinfo=UTC))
    assert early < late, "los identificadores deben ordenarse cronologicamente"


def test_list_models_is_newest_first(trained_model, tmp_path):
    _save(trained_model, tmp_path, model_id="tennis-logreg-20260101T000000Z")
    _save(trained_model, tmp_path, model_id="tennis-logreg-20260601T000000Z")

    manifests = list_models(tmp_path)
    assert len(manifests) == 2
    assert manifests[0].trained_at >= manifests[1].trained_at


def test_latest_model_on_empty_slot(tmp_path):
    assert latest_model(tmp_path) is None
    assert list_models(tmp_path) == []


def test_manifest_json_round_trip(trained_model, tmp_path):
    _, manifest = _save(trained_model, tmp_path)
    restored = ModelManifest.from_json(manifest.to_json())
    assert restored == manifest


@pytest.mark.critical
def test_production_and_challenger_are_separate_slots(trained_model, tmp_path):
    """Las dos ranuras no pueden mezclarse (brief §18)."""
    production = tmp_path / "production"
    challenger = tmp_path / "challenger"
    _save(trained_model, production, model_id="tennis-logreg-prod")
    _save(trained_model, challenger, model_id="tennis-logreg-chal")

    assert [m.model_id for m in list_models(production)] == ["tennis-logreg-prod"]
    assert [m.model_id for m in list_models(challenger)] == ["tennis-logreg-chal"]

"""Promocion manual de modelos a production (DEC-013): solo ficheros, con integridad."""

from __future__ import annotations

import pytest

from edgecourt.models import promotion
from edgecourt.models.registry import (
    ARTIFACT_SUFFIX,
    MANIFEST_SUFFIX,
    ModelIntegrityError,
    load_model,
    save_model,
)


def _save(directory, model_id="tennis-test-20260101T000000Z"):
    return save_model(
        {"weights": [1, 2, 3]},
        directory,
        model_type="test",
        train_first_year=2000,
        train_last_year=2022,
        n_train_rows=10,
        feature_names=["elo_diff"],
        hyperparameters={},
        model_id=model_id,
    )


def test_planning_a_promotion_writes_nothing(tmp_path):
    challenger, production = tmp_path / "challenger", tmp_path / "production"
    _save(challenger)

    plan = promotion.plan_promotion(
        "tennis-test-20260101T000000Z", challenger_dir=challenger, production_dir=production
    )

    assert plan.manifest.model_id == "tennis-test-20260101T000000Z"
    assert not production.exists()


@pytest.mark.critical
def test_promotion_copies_a_verified_model_and_keeps_the_challenger(tmp_path):
    challenger, production = tmp_path / "challenger", tmp_path / "production"
    _save(challenger)
    plan = promotion.plan_promotion(
        "tennis-test-20260101T000000Z", challenger_dir=challenger, production_dir=production
    )

    target = promotion.copy_to_production(plan)

    assert target == production / f"tennis-test-20260101T000000Z{ARTIFACT_SUFFIX}"
    assert (production / f"tennis-test-20260101T000000Z{MANIFEST_SUFFIX}").exists()
    model, manifest = load_model(production, "tennis-test-20260101T000000Z")
    assert model == {"weights": [1, 2, 3]}
    assert manifest.artifact_sha256 == plan.manifest.artifact_sha256
    assert (challenger / f"tennis-test-20260101T000000Z{ARTIFACT_SUFFIX}").exists()


@pytest.mark.critical
def test_a_tampered_challenger_is_not_promotable(tmp_path):
    challenger, production = tmp_path / "challenger", tmp_path / "production"
    artifact, _ = _save(challenger)
    artifact.write_bytes(artifact.read_bytes() + b"corrupto")

    with pytest.raises(ModelIntegrityError):
        promotion.plan_promotion(
            "tennis-test-20260101T000000Z", challenger_dir=challenger, production_dir=production
        )
    assert not production.exists()


def test_an_unknown_model_is_not_promotable(tmp_path):
    with pytest.raises(FileNotFoundError):
        promotion.plan_promotion(
            "no-existe", challenger_dir=tmp_path / "challenger", production_dir=tmp_path / "p"
        )

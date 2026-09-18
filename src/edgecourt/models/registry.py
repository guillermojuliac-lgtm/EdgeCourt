"""Guardado y carga de modelos, con manifiesto y verificacion de integridad.

Los binarios no se versionan en git (ver `.gitignore`): un modelo se identifica
por su manifiesto, que incluye el hash SHA-256 del artefacto. Cargar un modelo
comprueba ese hash, de modo que un fichero corrupto o sustituido se detecta en
lugar de producir predicciones silenciosamente distintas.

Dos ranuras (brief §18):

* `models/production/`  - genera las decisiones paper oficiales;
* `models/challenger/`  - solo produce predicciones paralelas.

Nada aqui promueve un challenger a produccion: eso requiere una persona.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import joblib

from edgecourt.logging_setup import get_logger
from edgecourt.models.base import ModelManifest, feature_hash, new_model_id, sha256_file

log = get_logger("models.registry")

Slot = Literal["production", "challenger"]

ARTIFACT_SUFFIX = ".joblib"
MANIFEST_SUFFIX = ".json"


class ModelIntegrityError(RuntimeError):
    """El artefacto no coincide con el hash declarado en su manifiesto."""


def save_model(
    model: Any,
    directory: Path,
    *,
    model_type: str,
    train_first_year: int,
    train_last_year: int,
    n_train_rows: int,
    feature_names: list[str],
    hyperparameters: dict[str, Any],
    validation_metrics: dict[str, Any] | None = None,
    notes: str = "",
    model_id: str | None = None,
) -> tuple[Path, ModelManifest]:
    """Persiste un modelo junto a su manifiesto. Devuelve (ruta, manifiesto)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    identifier = model_id or new_model_id(model_type)
    artifact_path = directory / f"{identifier}{ARTIFACT_SUFFIX}"
    joblib.dump(model, artifact_path)

    manifest = ModelManifest(
        model_id=identifier,
        model_type=model_type,
        trained_at=datetime.now(UTC).isoformat(),
        train_first_year=train_first_year,
        train_last_year=train_last_year,
        n_train_rows=n_train_rows,
        feature_names=list(feature_names),
        feature_hash=feature_hash(list(feature_names)),
        hyperparameters=hyperparameters,
        artifact_sha256=sha256_file(artifact_path),
        validation_metrics=validation_metrics or {},
        notes=notes,
    )
    (directory / f"{identifier}{MANIFEST_SUFFIX}").write_text(manifest.to_json(), encoding="utf-8")

    log.info(
        "modelo guardado",
        extra={"model_id": identifier, "slot": directory.name, "rows": n_train_rows},
    )
    return artifact_path, manifest


def load_model(directory: Path, model_id: str) -> tuple[Any, ModelManifest]:
    """Carga un modelo verificando su integridad."""
    directory = Path(directory)
    manifest_path = directory / f"{model_id}{MANIFEST_SUFFIX}"
    artifact_path = directory / f"{model_id}{ARTIFACT_SUFFIX}"

    if not manifest_path.exists():
        raise FileNotFoundError(f"No existe el manifiesto: {manifest_path}")
    if not artifact_path.exists():
        raise FileNotFoundError(f"No existe el artefacto: {artifact_path}")

    manifest = ModelManifest.from_json(manifest_path.read_text(encoding="utf-8"))
    actual = sha256_file(artifact_path)
    if manifest.artifact_sha256 and actual != manifest.artifact_sha256:
        raise ModelIntegrityError(
            f"El artefacto de '{model_id}' no coincide con su manifiesto "
            f"(esperado {manifest.artifact_sha256[:12]}..., encontrado {actual[:12]}...)"
        )

    return joblib.load(artifact_path), manifest


def list_models(directory: Path) -> list[ModelManifest]:
    """Manifiestos de una ranura, del mas reciente al mas antiguo."""
    directory = Path(directory)
    if not directory.exists():
        return []
    manifests = [
        ModelManifest.from_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob(f"*{MANIFEST_SUFFIX}"))
    ]
    return sorted(manifests, key=lambda m: m.trained_at, reverse=True)


def latest_model(directory: Path) -> tuple[Any, ModelManifest] | None:
    """El modelo mas reciente de una ranura, o None si esta vacia."""
    manifests = list_models(directory)
    if not manifests:
        return None
    return load_model(directory, manifests[0].model_id)

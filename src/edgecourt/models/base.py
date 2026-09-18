"""Contrato de un modelo de probabilidad y su versionado.

**El contrato es deliberadamente estrecho** (docs/ARCHITECTURE.md): un modelo
recibe features y devuelve probabilidades. No conoce cuotas, ni bankroll, ni
stake, ni siquiera que existe un mercado. Comparar con el precio es trabajo del
Value Engine, y decidir cuanto arriesgar es del Risk Engine.

Esa separacion no es estetica: si el modelo pudiera ver el precio, seria
imposible saber si un resultado bueno viene de predecir bien o de haber ajustado
contra el mercado.

Todo modelo entrenado se acompana de un manifiesto con el hash de su artefacto,
la lista de features y el rango temporal de los datos, de modo que cualquier
prediccion registrada en el ledger sea atribuible a un modelo concreto.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd

PROBABILITY_A = "probability_player_a"
PROBABILITY_B = "probability_player_b"

# Tolerancia al comprobar que las probabilidades suman 1.
PROBABILITY_SUM_TOLERANCE = 1e-9


@runtime_checkable
class ProbabilityModel(Protocol):
    """Lo unico que EdgeCourt exige de un modelo."""

    name: str

    def predict_proba(self, features: pd.DataFrame) -> pd.DataFrame:
        """Devuelve un DataFrame con `probability_player_a` y `_b`, que suman 1."""
        ...


def validate_probabilities(frame: pd.DataFrame) -> pd.DataFrame:
    """Comprueba el contrato de salida. Falla ruidosamente si no se cumple.

    Una probabilidad fuera de rango o un par que no suma 1 invalidaria todo lo
    que venga despues -calibracion, edge, stake-, asi que se detecta aqui y no
    tres capas mas abajo.
    """
    missing = {PROBABILITY_A, PROBABILITY_B} - set(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas de probabilidad: {sorted(missing)}")

    for column in (PROBABILITY_A, PROBABILITY_B):
        values = frame[column]
        if values.isna().any():
            raise ValueError(f"'{column}' contiene nulos")
        if not values.between(0.0, 1.0).all():
            raise ValueError(f"'{column}' tiene valores fuera de [0, 1]")

    total = frame[PROBABILITY_A] + frame[PROBABILITY_B]
    if not ((total - 1.0).abs() <= PROBABILITY_SUM_TOLERANCE).all():
        worst = float((total - 1.0).abs().max())
        raise ValueError(f"P(A) + P(B) != 1 (desviacion maxima {worst:.2e})")

    return frame


def as_probability_frame(probability_a, index: pd.Index) -> pd.DataFrame:
    """Construye la salida canonica a partir de P(A)."""
    p_a = pd.Series(probability_a, index=index, dtype=float)
    return validate_probabilities(
        pd.DataFrame({PROBABILITY_A: p_a, PROBABILITY_B: 1.0 - p_a}, index=index)
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def feature_hash(feature_names: list[str]) -> str:
    """Huella de la lista de features, en orden.

    Sirve para detectar que un modelo se esta usando con un conjunto de features
    distinto del que vio al entrenarse, que es una via silenciosa de error.
    """
    payload = "|".join(feature_names).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ModelManifest:
    """Ficha de identidad de un modelo entrenado."""

    model_id: str
    model_type: str
    trained_at: str
    train_first_year: int
    train_last_year: int
    n_train_rows: int
    feature_names: list[str]
    feature_hash: str
    hyperparameters: dict[str, Any]
    artifact_sha256: str = ""
    validation_metrics: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)

    @classmethod
    def from_json(cls, payload: str) -> ModelManifest:
        return cls(**json.loads(payload))


def new_model_id(model_type: str, *, trained_at: datetime | None = None) -> str:
    """Identificador legible y ordenable: `tennis-logreg-20260918T081500Z`."""
    stamp = (trained_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"tennis-{model_type}-{stamp}"

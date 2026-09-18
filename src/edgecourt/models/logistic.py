"""MODEL 2: regresion logistica.

Baseline lineal e interpretable sobre las features de PHASE 3.

**Diseno antisimetrico por construccion.** El problema tiene una simetria que la
mayoria de implementaciones ignoran y que aqui se explota: como la asignacion
A/B es aleatoria, intercambiar los lados debe invertir exactamente la
prediccion, es decir `P(A | -X) = 1 - P(A | X)`. Tres decisiones lo garantizan:

* **Sin intercepto** (`fit_intercept=False`). Un intercepto distinto de cero
  significaria "el lado A gana mas a menudo", que es falso por construccion.
* **Imputacion con 0**, no con la mediana. Todas las features son diferencias
  A-B: un 0 significa "sin diferencia conocida", que es exactamente lo que se
  quiere decir cuando falta el dato. La mediana introduciria un sesgo de lado.
* **Escalado sin centrar** (`with_mean=False`). Restar una media no nula
  desplazaria el origen y romperia la simetria.

Consecuencia practica: el modelo no puede aprender un sesgo hacia el lado A
aunque quisiera, y eso elimina toda una familia de errores silenciosos.

**Las features de contexto (superficie, ronda, nivel de torneo) quedan fuera de
este modelo.** No es un olvido: en un problema antisimetrico, una variable que
no distingue entre A y B no puede aportar informacion sobre quien gana. Si el
modelo aprendiese "en tierra gana A con probabilidad 0,52", estaria aprendiendo
ruido, porque A es una etiqueta lanzada a cara o cruz. El contexto solo puede
aportar en interaccion con features antisimetricas -por ejemplo, que el Elo de
superficie pese mas en tierra-, y eso se explora aparte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from edgecourt.features.builder import FEATURE_NAMES
from edgecourt.logging_setup import get_logger
from edgecourt.models.base import as_probability_frame

log = get_logger("models.logistic")

# Features numericas antisimetricas: las de PHASE 3 mas las diferencias de Elo.
ELO_FEATURES: tuple[str, ...] = ("elo_diff", "surface_elo_diff")
DEFAULT_FEATURES: tuple[str, ...] = tuple(FEATURE_NAMES) + ELO_FEATURES


@dataclass(slots=True)
class LogisticConfig:
    """Hiperparametros. `C` se elige sobre VALIDATION, nunca sobre TEST."""

    C: float = 1.0
    # scikit-learn 1.8 deprecio `penalty`: la regularizacion se expresa ahora con
    # `l1_ratio`, donde 0.0 equivale a L2 pura.
    l1_ratio: float = 0.0
    max_iter: int = 2000
    solver: str = "lbfgs"

    def as_dict(self) -> dict[str, Any]:
        return {
            "C": self.C,
            "l1_ratio": self.l1_ratio,
            "max_iter": self.max_iter,
            "solver": self.solver,
            "fit_intercept": False,
        }


@dataclass(slots=True)
class LogisticModel:
    """Regresion logistica sobre features antisimetricas."""

    name: str = "logistic_regression"
    config: LogisticConfig = field(default_factory=LogisticConfig)
    feature_names: tuple[str, ...] = DEFAULT_FEATURES
    pipeline: Pipeline | None = None

    def build_pipeline(self) -> Pipeline:
        return Pipeline(
            steps=[
                # 0 = "sin diferencia conocida". Preserva la antisimetria.
                ("impute", SimpleImputer(strategy="constant", fill_value=0.0)),
                # Sin centrar: centrar desplazaria el origen y romperia la simetria.
                ("scale", StandardScaler(with_mean=False)),
                (
                    "model",
                    LogisticRegression(
                        C=self.config.C,
                        l1_ratio=self.config.l1_ratio,
                        max_iter=self.config.max_iter,
                        solver=self.config.solver,
                        fit_intercept=False,
                    ),
                ),
            ]
        )

    def _matrix(self, features: pd.DataFrame) -> pd.DataFrame:
        missing = set(self.feature_names) - set(features.columns)
        if missing:
            raise ValueError(f"Faltan features requeridas por el modelo: {sorted(missing)}")
        return features[list(self.feature_names)].astype(float)

    def fit(self, features: pd.DataFrame, target: pd.Series) -> LogisticModel:
        """Entrena. **Solo debe llamarse con datos de TRAIN.**"""
        matrix = self._matrix(features)
        self.pipeline = self.build_pipeline()
        self.pipeline.fit(matrix, np.asarray(target, dtype=int))
        log.info(
            "modelo entrenado",
            extra={
                "rows": len(matrix),
                "features": len(self.feature_names),
                "C": self.config.C,
            },
        )
        return self

    def predict_proba(self, features: pd.DataFrame) -> pd.DataFrame:
        """Probabilidad de que gane el jugador A. Cumple P(A) + P(B) = 1."""
        if self.pipeline is None:
            raise RuntimeError("El modelo no esta entrenado")
        matrix = self._matrix(features)
        probability_a = self.pipeline.predict_proba(matrix)[:, 1]
        return as_probability_frame(probability_a, features.index)

    def coefficients(self) -> pd.DataFrame:
        """Pesos aprendidos, para poder interpretar el modelo."""
        if self.pipeline is None:
            raise RuntimeError("El modelo no esta entrenado")
        estimator = self.pipeline.named_steps["model"]
        scaler = self.pipeline.named_steps["scale"]
        # Se deshace el escalado para expresar el peso en unidades originales.
        raw = estimator.coef_[0] / scaler.scale_
        return (
            pd.DataFrame(
                {
                    "feature": list(self.feature_names),
                    "coefficient_scaled": estimator.coef_[0],
                    "coefficient_raw": raw,
                }
            )
            .assign(abs_scaled=lambda d: d["coefficient_scaled"].abs())
            .sort_values("abs_scaled", ascending=False)
            .drop(columns=["abs_scaled"])
            .reset_index(drop=True)
        )

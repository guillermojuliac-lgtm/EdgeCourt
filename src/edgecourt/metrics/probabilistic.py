"""Metricas de calidad probabilistica.

Se implementan con numpy en lugar de traer scikit-learn: son pocas lineas, no
tienen ambiguedad y evitan una dependencia antes de necesitarla de verdad
(el brief pide comprobar que una dependencia es necesaria antes de instalarla).

Jerarquia de metricas (docs/METRICS.md): la calibracion y el Brier mandan; la
accuracy se reporta **solo como referencia** porque un modelo puede acertar
mucho y estar mal calibrado, y para apostar lo que importa es la probabilidad.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Recorte para evitar log(0) en el Log Loss.
EPSILON = 1e-15

# Buckets de probabilidad para la curva de calibracion.
DEFAULT_BUCKETS: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def _as_arrays(y_true, y_prob) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    if y.shape != p.shape:
        raise ValueError(f"Dimensiones distintas: y_true {y.shape}, y_prob {p.shape}")
    if len(y) == 0:
        raise ValueError("No hay observaciones que evaluar")
    if not np.all(np.isin(y[~np.isnan(y)], (0.0, 1.0))):
        raise ValueError("y_true debe contener solo 0 y 1")
    finite = ~np.isnan(y) & ~np.isnan(p)
    return y[finite], p[finite]


def brier_score(y_true, y_prob) -> float:
    """Error cuadratico medio de la probabilidad. Menor es mejor; 0.25 = moneda."""
    y, p = _as_arrays(y_true, y_prob)
    return float(np.mean((p - y) ** 2))


def log_loss(y_true, y_prob) -> float:
    """Penaliza la confianza equivocada. Menor es mejor; ~0.6931 = moneda."""
    y, p = _as_arrays(y_true, y_prob)
    p = np.clip(p, EPSILON, 1.0 - EPSILON)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def accuracy(y_true, y_prob, threshold: float = 0.5) -> float:
    """Proporcion de aciertos. **Solo referencia**: no se optimiza."""
    y, p = _as_arrays(y_true, y_prob)
    return float(np.mean((p >= threshold).astype(float) == y))


def _rankdata(values: np.ndarray) -> np.ndarray:
    """Rangos con promedio en los empates (equivalente a scipy.stats.rankdata)."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1, dtype=float)

    sorted_values = values[order]
    start = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or sorted_values[i] != sorted_values[start]:
            if i - start > 1:
                ranks[order[start:i]] = (start + 1 + i) / 2.0
            start = i
    return ranks


def roc_auc(y_true, y_prob) -> float:
    """Capacidad de ordenacion. Se reporta, pero no se optimiza."""
    y, p = _as_arrays(y_true, y_prob)
    positives = y == 1.0
    n_pos = int(positives.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = _rankdata(p)
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def brier_skill_score(y_true, y_prob, y_prob_reference) -> float:
    """Mejora relativa del Brier frente a una referencia.

    Positivo = mejor que la referencia. La referencia por defecto del proyecto es
    **el mercado**, no la moneda: superar a la moneda no demuestra nada.
    """
    return 1.0 - brier_score(y_true, y_prob) / brier_score(y_true, y_prob_reference)


def calibration_table(y_true, y_prob, buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> pd.DataFrame:
    """Curva de calibracion por buckets de probabilidad.

    Para cada bucket compara la probabilidad media predicha con la frecuencia
    observada. El tamano de cada bucket se reporta siempre: una desviacion sobre
    30 partidos no significa lo mismo que sobre 3.000.
    """
    y, p = _as_arrays(y_true, y_prob)
    edges = np.asarray(buckets, dtype=float)
    # `right=False` con el ultimo bucket cerrado, para que p=1.0 no quede fuera.
    index = np.clip(np.digitize(p, edges[1:-1], right=False), 0, len(edges) - 2)

    rows = []
    for i in range(len(edges) - 1):
        mask = index == i
        n = int(mask.sum())
        if n == 0:
            continue
        predicted = float(p[mask].mean())
        observed = float(y[mask].mean())
        rows.append(
            {
                "bucket": f"[{edges[i]:.1f}, {edges[i + 1]:.1f})",
                "n": n,
                "pct_of_total": round(100.0 * n / len(y), 1),
                "mean_predicted": round(predicted, 4),
                "observed_freq": round(observed, 4),
                "gap": round(observed - predicted, 4),
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(
    y_true, y_prob, buckets: tuple[float, ...] = DEFAULT_BUCKETS
) -> float:
    """ECE: desviacion media entre lo predicho y lo observado, ponderada por tamano."""
    table = calibration_table(y_true, y_prob, buckets)
    if table.empty:
        return float("nan")
    weights = table["n"] / table["n"].sum()
    return float((weights * table["gap"].abs()).sum())


@dataclass(frozen=True, slots=True)
class ProbabilisticReport:
    """Resumen de evaluacion de un conjunto de predicciones."""

    name: str
    n: int
    brier: float
    log_loss: float
    accuracy: float
    roc_auc: float
    ece: float

    def as_row(self) -> dict[str, object]:
        return {
            "model": self.name,
            "n": self.n,
            "brier": round(self.brier, 5),
            "log_loss": round(self.log_loss, 5),
            "accuracy": round(self.accuracy, 4),
            "roc_auc": round(self.roc_auc, 4),
            "ece": round(self.ece, 4),
        }


def evaluate(y_true, y_prob, name: str) -> ProbabilisticReport:
    """Calcula el conjunto completo de metricas para unas predicciones."""
    y, p = _as_arrays(y_true, y_prob)
    return ProbabilisticReport(
        name=name,
        n=len(y),
        brier=brier_score(y, p),
        log_loss=log_loss(y, p),
        accuracy=accuracy(y, p),
        roc_auc=roc_auc(y, p),
        ece=expected_calibration_error(y, p),
    )


# Referencias fijas para interpretar los numeros.
COIN_FLIP_BRIER = 0.25
COIN_FLIP_LOG_LOSS = float(-np.log(0.5))

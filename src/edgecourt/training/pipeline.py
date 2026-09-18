"""Entrenamiento y evaluacion de modelos de probabilidad.

Disciplina temporal, que es lo que hace que los numeros signifiquen algo:

* se entrena **solo** con TRAIN;
* los hiperparametros se eligen **solo** sobre VALIDATION, con presupuesto de
  iteraciones registrado en el manifiesto;
* TEST no interviene en ninguna decision, y cada consulta queda anotada en
  `data/results/test_evaluations.jsonl`.

La comparacion contra los benchmarks Elo se hace **sobre exactamente las mismas
filas**: mismo filtro de experiencia, mismas exclusiones. Comparar un modelo
sobre un subconjunto distinto del benchmark no compara nada.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from edgecourt.config import Settings
from edgecourt.data import splits
from edgecourt.features.elo import elo_probability
from edgecourt.logging_setup import get_logger
from edgecourt.metrics import probabilistic as metrics
from edgecourt.models.base import PROBABILITY_A
from edgecourt.models.logistic import DEFAULT_FEATURES, LogisticConfig, LogisticModel
from edgecourt.models.registry import save_model
from edgecourt.storage import read_parquet

log = get_logger("training")

FEATURES_DATASET = "features"
MATCHES_DATASET = "matches"
ELO_DATASET = "elo"

# Rejilla de busqueda, declarada de antemano y deliberadamente corta: cuanto mas
# amplia, mas probable es encontrar un buen resultado por azar sobre VALIDATION.
# El presupuesto queda registrado en el manifiesto del modelo.
#
# Se extendio una vez hacia valores mas pequenos, al comprobar que el optimo caia
# en el borde inferior; con esta rejilla el optimo queda en el interior, que es
# la condicion minima para poder decir que se ha encontrado un optimo.
DEFAULT_C_GRID: tuple[float, ...] = (
    0.00003,
    0.0001,
    0.0003,
    0.001,
    0.003,
    0.01,
    0.1,
    1.0,
)

# Mismo filtro que uso el benchmark Elo en PHASE 2, para que la comparacion sea
# exactamente sobre las mismas filas.
DEFAULT_MIN_MATCHES = 10


@dataclass(slots=True)
class TrainingResult:
    model: LogisticModel
    model_id: str
    manifest: Any
    n_train: int
    search: pd.DataFrame
    coefficients: pd.DataFrame


def load_modelling_frame(
    settings: Settings, *, min_matches: int = DEFAULT_MIN_MATCHES
) -> pd.DataFrame:
    """Une features, resultado y Elo, y aplica los filtros de evaluacion.

    Los filtros son los mismos que en el benchmark Elo: sin walkovers y con al
    menos `min_matches` partidos previos de ambos jugadores. Un partido cuyo
    jugador debuta no mide el modelo, mide el valor por defecto.
    """
    features = read_parquet(settings.processed_dir / FEATURES_DATASET)
    outcomes = read_parquet(
        settings.processed_dir / MATCHES_DATASET,
        columns=["match_id", "target", "completion_status", "surface", "tourney_level"],
    )
    elo = read_parquet(
        settings.processed_dir / ELO_DATASET,
        columns=["match_id", "elo_a_matches_before", "elo_b_matches_before"],
    )

    frame = features.merge(outcomes, on="match_id", how="inner", suffixes=("", "_outcome"))
    frame = frame.merge(elo, on="match_id", how="left", suffixes=("", "_elo"))
    frame["split"] = splits.assign_split(frame["year"])

    frame = frame[frame["completion_status"] != "walkover"]
    if min_matches > 0:
        experienced = (frame["elo_a_matches_before_elo"].fillna(0) >= min_matches) & (
            frame["elo_b_matches_before_elo"].fillna(0) >= min_matches
        )
        frame = frame[experienced]

    return frame.reset_index(drop=True)


def _evaluate(frame: pd.DataFrame, probability: pd.Series, name: str) -> dict[str, Any]:
    return metrics.evaluate(frame["target"], probability, name).as_row()


def search_hyperparameters(
    frame: pd.DataFrame,
    *,
    c_grid: tuple[float, ...] = DEFAULT_C_GRID,
    features: tuple[str, ...] = DEFAULT_FEATURES,
) -> pd.DataFrame:
    """Elige `C` **sobre VALIDATION**. TEST no participa.

    Se selecciona por Log Loss y no por accuracy: lo que importa es la calidad de
    la probabilidad, no cuantas veces se acierta el favorito.
    """
    train = frame[frame["split"] == "train"]
    validation = frame[frame["split"] == "validation"]
    if train.empty or validation.empty:
        raise ValueError("Se necesitan filas de TRAIN y de VALIDATION para buscar C")

    rows: list[dict[str, Any]] = []
    for c_value in c_grid:
        model = LogisticModel(config=LogisticConfig(C=c_value), feature_names=features)
        model.fit(train, train["target"])
        predictions = model.predict_proba(validation)[PROBABILITY_A]
        row = _evaluate(validation, predictions, f"C={c_value}")
        row["C"] = c_value
        rows.append(row)

    search = pd.DataFrame(rows).sort_values("log_loss").reset_index(drop=True)
    log.info(
        "busqueda de hiperparametros completada",
        extra={"candidates": len(c_grid), "best_C": float(search.iloc[0]["C"])},
    )
    return search


def train_logistic(
    settings: Settings,
    *,
    min_matches: int = DEFAULT_MIN_MATCHES,
    c_grid: tuple[float, ...] = DEFAULT_C_GRID,
    slot: str = "challenger",
) -> TrainingResult:
    """Entrena la regresion logistica y la guarda en la ranura indicada.

    Por defecto va a `challenger`: nada entra en produccion automaticamente.
    """
    frame = load_modelling_frame(settings, min_matches=min_matches)
    train = frame[frame["split"] == "train"]
    validation = frame[frame["split"] == "validation"]

    search = search_hyperparameters(frame, c_grid=c_grid)
    best_c = float(search.iloc[0]["C"])

    model = LogisticModel(config=LogisticConfig(C=best_c))
    model.fit(train, train["target"])

    validation_metrics = _evaluate(
        validation, model.predict_proba(validation)[PROBABILITY_A], "logistic_regression"
    )

    directory = settings.models_dir / slot
    _, manifest = save_model(
        model,
        directory,
        model_type="logreg",
        train_first_year=int(train["year"].min()),
        train_last_year=int(train["year"].max()),
        n_train_rows=len(train),
        feature_names=list(model.feature_names),
        hyperparameters=model.config.as_dict()
        | {"search_budget": len(c_grid), "search_grid": list(c_grid)},
        validation_metrics=validation_metrics,
        notes=(
            "Antisimetrico por construccion: sin intercepto, imputacion a 0 y escalado "
            "sin centrar. Hiperparametro C elegido solo sobre VALIDATION."
        ),
    )

    return TrainingResult(
        model=model,
        model_id=manifest.model_id,
        manifest=manifest,
        n_train=len(train),
        search=search,
        coefficients=model.coefficients(),
    )


def compare_against_elo(
    settings: Settings,
    model: LogisticModel,
    *,
    split_names: tuple[str, ...] = ("validation", "test"),
    min_matches: int = DEFAULT_MIN_MATCHES,
    model_id: str = "",
) -> dict[str, Any]:
    """Compara el modelo con los benchmarks Elo sobre **las mismas filas**.

    Devuelve, por conjunto, la tabla de metricas y las curvas de calibracion.
    """
    frame = load_modelling_frame(settings, min_matches=min_matches)
    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model_id": model_id,
        "min_matches_before": min_matches,
        "splits": {},
    }

    for split_name in split_names:
        subset = frame[frame["split"] == split_name]
        if subset.empty:
            raise ValueError(f"El conjunto '{split_name}' no contiene partidos")

        predictions = {
            "logistic_regression": model.predict_proba(subset)[PROBABILITY_A],
            "elo_global": elo_probability(subset, surface_weight=0.0),
            "elo_surface": elo_probability(subset, surface_weight=1.0),
            "elo_blend_50": elo_probability(subset, surface_weight=0.5),
            "moneda": pd.Series(0.5, index=subset.index),
        }

        rows = [_evaluate(subset, values, name) for name, values in predictions.items()]
        table = pd.DataFrame(rows)
        coin_brier = float(table.loc[table["model"] == "moneda", "brier"].iloc[0])
        table["brier_skill_vs_coin"] = (1.0 - table["brier"] / coin_brier).round(4)

        elo_brier = float(table.loc[table["model"] == "elo_global", "brier"].iloc[0])
        table["brier_skill_vs_elo"] = (1.0 - table["brier"] / elo_brier).round(4)

        calibration = {
            name: metrics.calibration_table(subset["target"], values).to_dict(orient="records")
            for name, values in predictions.items()
            if name != "moneda"
        }

        results["splits"][split_name] = {
            "n_matches": len(subset),
            "metrics": table.to_dict(orient="records"),
            "calibration": calibration,
        }

        if split_name == "test":
            splits.record_test_evaluation(
                settings.results_dir,
                model=model_id or "logistic_regression",
                reason="PHASE 4: comparacion de la regresion logistica con los benchmarks Elo",
                metrics=table.to_dict(orient="records"),
            )

    path = settings.results_dir / "logistic_benchmark.json"
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    log.info("informe de comparacion escrito", extra={"path": str(path)})
    return results

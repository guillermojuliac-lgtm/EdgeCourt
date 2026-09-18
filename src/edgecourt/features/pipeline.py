"""Orquestacion del Elo: calculo, persistencia y evaluacion como benchmark."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from edgecourt.config import Settings
from edgecourt.data import splits
from edgecourt.features.builder import build_features, feature_coverage
from edgecourt.features.elo import EloConfig, compute_elo, elo_probability
from edgecourt.logging_setup import get_logger
from edgecourt.metrics import probabilistic as metrics
from edgecourt.storage import read_parquet, write_partitioned_parquet

log = get_logger("features.pipeline")

ELO_DATASET = "elo"
FEATURES_DATASET = "features"
MATCHES_DATASET = "matches"

# Variantes del benchmark Elo que se comparan entre si.
ELO_VARIANTS: dict[str, float] = {
    "elo_global": 0.0,
    "elo_blend_50": 0.5,
    "elo_surface": 1.0,
}


@dataclass(slots=True)
class EloBuildResult:
    rows: int
    destination: Path
    players: int
    first_year: int
    last_year: int


@dataclass(slots=True)
class FeatureBuildResult:
    rows: int
    features: int
    destination: Path
    coverage: pd.DataFrame


def _ordered_matches(settings: Settings) -> pd.DataFrame:
    """Carga `match_facts` en el unico orden que el calculo secuencial admite."""
    matches = read_parquet(settings.processed_dir / MATCHES_DATASET)
    return matches.sort_values(
        ["date", "tourney_id", "round_order", "match_num"], kind="mergesort", na_position="last"
    ).reset_index(drop=True)


def build_feature_table(settings: Settings) -> FeatureBuildResult:
    """Genera la tabla de features y la une con los ratings Elo.

    El Elo se une por `match_id`: ya se calculo con la misma garantia de orden y
    repetirlo aqui seria duplicar codigo y riesgo.
    """
    matches = _ordered_matches(settings)
    features = build_features(matches)

    elo = read_parquet(settings.processed_dir / ELO_DATASET)
    elo_columns = [
        "match_id",
        "elo_diff",
        "surface_elo_diff",
        "elo_a_matches_before",
        "elo_b_matches_before",
    ]
    features = features.merge(elo[elo_columns], on="match_id", how="left")

    destination = settings.processed_dir / FEATURES_DATASET
    write_partitioned_parquet(features, destination, partition_cols=["year"])

    coverage = feature_coverage(features)
    _write_report(
        settings,
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "rows": len(features),
            "feature_count": len(coverage),
            "coverage": coverage.to_dict(orient="records"),
        },
        "feature_report.json",
    )

    return FeatureBuildResult(
        rows=len(features),
        features=len(coverage),
        destination=destination,
        coverage=coverage,
    )


def _write_report(settings: Settings, payload: dict, name: str) -> Path:
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    path = settings.results_dir / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("informe escrito", extra={"path": str(path)})
    return path


def build_elo(settings: Settings, config: EloConfig | None = None) -> EloBuildResult:
    """Calcula el Elo sobre todo el historico disponible y lo persiste.

    Se usa el historico **completo**, no solo el rango de entrenamiento: los
    ratings necesitan partidos previos para dejar de ser 1500. Esto no es
    leakage -cada partido solo ve su pasado- pero si es necesario para que los
    ratings de los primeros anos evaluados esten maduros.
    """
    matches = _ordered_matches(settings)
    elo = compute_elo(matches, config)
    destination = settings.processed_dir / ELO_DATASET
    write_partitioned_parquet(elo, destination, partition_cols=["year"])

    players = pd.concat([matches["player_a_id"], matches["player_b_id"]]).nunique()
    return EloBuildResult(
        rows=len(elo),
        destination=destination,
        players=int(players),
        first_year=int(elo["year"].min()),
        last_year=int(elo["year"].max()),
    )


def load_evaluation_frame(settings: Settings, *, min_matches: int = 0) -> pd.DataFrame:
    """Une `match_facts` con los ratings previos y deja el frame listo para evaluar.

    `min_matches` descarta partidos en los que alguno de los dos jugadores tiene
    menos de N partidos previos: su rating sigue siendo practicamente el inicial
    y evaluar sobre ellos mide sobre todo el valor por defecto, no el modelo.
    """
    matches = read_parquet(
        settings.processed_dir / MATCHES_DATASET,
        columns=["match_id", "year", "target", "surface", "completion_status", "tourney_level"],
    )
    elo = read_parquet(settings.processed_dir / ELO_DATASET)

    frame = matches.merge(elo.drop(columns=["year", "surface"]), on="match_id", how="inner")
    frame["split"] = splits.assign_split(frame["year"])

    if min_matches > 0:
        experienced = (frame["elo_a_matches_before"] >= min_matches) & (
            frame["elo_b_matches_before"] >= min_matches
        )
        frame = frame[experienced]

    # Los partidos no disputados no se evaluan: no hay nada que predecir.
    return frame[frame["completion_status"] != "walkover"].reset_index(drop=True)


def evaluate_split(
    frame: pd.DataFrame, split_name: splits.SplitName, *, config: EloConfig | None = None
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Evalua todas las variantes de Elo sobre un conjunto.

    Returns:
        (tabla de metricas, tablas de calibracion por variante)
    """
    subset = frame[frame["split"] == split_name]
    if subset.empty:
        raise ValueError(f"El conjunto '{split_name}' no contiene partidos")

    rows: list[dict[str, object]] = []
    calibrations: dict[str, pd.DataFrame] = {}

    for name, weight in ELO_VARIANTS.items():
        probability = elo_probability(subset, surface_weight=weight, config=config)
        report = metrics.evaluate(subset["target"], probability, name)
        rows.append(report.as_row())
        calibrations[name] = metrics.calibration_table(subset["target"], probability)

    # Referencias: la moneda y el ranking ATP, para situar los numeros.
    coin = pd.Series(0.5, index=subset.index)
    rows.append(metrics.evaluate(subset["target"], coin, "moneda").as_row())

    table = pd.DataFrame(rows)
    best = table.loc[table["brier"].idxmin(), "model"]
    coin_brier = float(table.loc[table["model"] == "moneda", "brier"].iloc[0])
    table["brier_skill_vs_coin"] = (1.0 - table["brier"] / coin_brier).round(4)

    log.info(
        "evaluacion completada",
        extra={"split": split_name, "n": len(subset), "best": best},
    )
    return table, calibrations


def evaluate_elo(
    settings: Settings,
    *,
    split_names: tuple[splits.SplitName, ...] = ("validation", "test"),
    min_matches: int = 10,
    config: EloConfig | None = None,
) -> dict[str, object]:
    """Evalua el benchmark Elo y escribe el informe."""
    frame = load_evaluation_frame(settings, min_matches=min_matches)

    results: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "min_matches_before": min_matches,
        "splits": {},
    }

    for split_name in split_names:
        table, calibrations = evaluate_split(frame, split_name, config=config)
        results["splits"][split_name] = {
            "n_matches": int((frame["split"] == split_name).sum()),
            "metrics": table.to_dict(orient="records"),
            "calibration": {k: v.to_dict(orient="records") for k, v in calibrations.items()},
        }

        if split_name == "test":
            splits.record_test_evaluation(
                settings.results_dir,
                model="elo-benchmark-v1",
                reason="PHASE 2: establecer el benchmark Elo",
                metrics=table.to_dict(orient="records"),
            )

    _write_report(settings, results, "elo_benchmark.json")
    return results

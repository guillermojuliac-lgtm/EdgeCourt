"""Predicciones para mercados proximos (Carril A, paso A3). Funciones puras, sin base de datos.

El modelo solo recibe features y devuelve probabilidades: no ve cuotas ni precios
(docs/ARCHITECTURE.md). La observacion del mercado se guarda unicamente como ancla de la
prediccion en el ledger, para que el valor (A4) compare con el precio de ESE momento.

Un mercado se predice solo si cumple todo esto; si no, se excluye y se CUENTA el motivo
(nunca se descarta en silencio):

* identidad lista (A2): dos jugadores resueltos de forma determinista y superficie conocida;
* `betfair_runner.player_id` ya rellenado y coincidente con la resolucion;
* una observacion reciente con precios;
* historial minimo de ambos jugadores;
* fecha del partido no anterior al ultimo partido de los datos (seria fuga temporal).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from edgecourt.features import asof
from edgecourt.identity import service as identity_service
from edgecourt.identity.players import PlayerIndex
from edgecourt.identity.tournaments import SurfaceIndex
from edgecourt.models.base import PROBABILITY_A, ModelManifest, ProbabilityModel, feature_hash

NO_OBSERVATION = "sin_observacion_reciente"
NO_PLAYER_ID = "sin_player_id"
INCONSISTENT_IDENTITY = "identidad_inconsistente"
THIN_HISTORY = "historial_insuficiente"
DATE_BEFORE_DATA = "fecha_anterior_a_los_datos"

PROBABILITY_DECIMALS = 6  # prediction.probability es numeric(8,6)


@dataclass(frozen=True, slots=True)
class PredictionRow:
    """Una fila de `prediction`: la probabilidad de UN corredor en UNA observacion."""

    model_id: str
    observation_id: int
    observed_at: datetime
    market_id: str
    selection_id: int
    probability: float
    feature_hash: str
    features: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MarketPrediction:
    """Resumen legible de un mercado predicho."""

    market_id: str
    start_time: datetime
    competition: str
    name_a: str
    name_b: str
    probability_a: float
    probability_b: float


@dataclass(slots=True)
class PredictionReport:
    model_id: str
    data_as_of: pd.Timestamp | None
    markets_seen: int
    rows: list[PredictionRow] = field(default_factory=list)
    markets: list[MarketPrediction] = field(default_factory=list)
    excluded: Counter = field(default_factory=Counter)

    @property
    def predicted(self) -> int:
        return len(self.markets)


def predict_markets(
    rows: list[dict[str, Any]],
    *,
    state: asof.AsOfState,
    model: ProbabilityModel,
    manifest: ModelManifest,
    player_index: PlayerIndex,
    surface_index: SurfaceIndex,
    min_matches: int = 10,
) -> PredictionReport:
    """Predice los mercados de `rows` (un registro por corredor, ver `markets_for_prediction`)."""
    _check_model(model, manifest)

    # Orden estable: el corredor A de cada mercado es siempre el de menor selection_id, y el
    # servicio de identidad conserva ese mismo orden en `market.runners`.
    rows = sorted(rows, key=lambda r: (r["market_id"], r["selection_id"]))
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[row["market_id"]].append(row)

    identity = identity_service.resolve_markets(rows, player_index, surface_index)
    report = PredictionReport(manifest.model_id, state.data_as_of, len(identity.markets))

    pending: list[
        tuple[identity_service.MarketIdentity, list[dict[str, Any]], asof.AsOfFeatures]
    ] = []
    for market in identity.markets:
        market_rows = by_market[market.market_id]
        reason = _exclusion_reason(market, market_rows)
        as_of = _as_of_date(market_rows[0]["market_start_time"])
        if reason is None and state.data_as_of is not None and as_of < state.data_as_of:
            reason = DATE_BEFORE_DATA
        if reason is not None:
            report.excluded[reason] += 1
            continue

        ids = [resolution.player_id for _, _, resolution in market.runners]
        result = asof.features_for_pair(state, ids[0], ids[1], surface=market.surface, as_of=as_of)
        if min(result.player_a_matches_before, result.player_b_matches_before) < min_matches:
            report.excluded[THIN_HISTORY] += 1
            continue
        pending.append((market, market_rows, result))

    if not pending:
        return report

    frame = pd.DataFrame([result.values for _, _, result in pending])
    probabilities = model.predict_proba(frame)[PROBABILITY_A].tolist()

    for (market, market_rows, result), raw_a in zip(pending, probabilities, strict=True):
        p_a = round(float(raw_a), PROBABILITY_DECIMALS)
        p_b = round(1.0 - p_a, PROBABILITY_DECIMALS)
        first, second = market_rows
        features = _stored_features(result, manifest, market, first["selection_id"])
        for runner_row, probability in ((first, p_a), (second, p_b)):
            report.rows.append(
                PredictionRow(
                    model_id=manifest.model_id,
                    observation_id=runner_row["observation_id"],
                    observed_at=runner_row["observed_at"],
                    market_id=market.market_id,
                    selection_id=runner_row["selection_id"],
                    probability=probability,
                    feature_hash=manifest.feature_hash,
                    features=features,
                )
            )
        report.markets.append(
            MarketPrediction(
                market_id=market.market_id,
                start_time=first["market_start_time"],
                competition=market.competition,
                name_a=first["runner_name"],
                name_b=second["runner_name"],
                probability_a=p_a,
                probability_b=p_b,
            )
        )
    return report


def _check_model(model: ProbabilityModel, manifest: ModelManifest) -> None:
    """El modelo debe usar exactamente las features que declara su manifiesto."""
    missing = [name for name in manifest.feature_names if name not in asof.ALL_FEATURE_NAMES]
    if missing:
        raise ValueError(f"el modelo pide features que A1 no calcula: {missing}")
    if manifest.feature_hash != feature_hash(list(manifest.feature_names)):
        raise ValueError("el feature_hash del manifiesto no coincide con su lista de features")
    declared = getattr(model, "feature_names", None)
    if declared is not None and list(declared) != list(manifest.feature_names):
        raise ValueError("las features del modelo cargado difieren de las de su manifiesto")


def _exclusion_reason(
    market: identity_service.MarketIdentity, market_rows: list[dict[str, Any]]
) -> str | None:
    if market.status != identity_service.READY:
        return market.status
    if any(row["observation_id"] is None for row in market_rows):
        return NO_OBSERVATION
    if any(row["player_id"] is None for row in market_rows):
        return NO_PLAYER_ID
    resolved = {sid: res.player_id for sid, _, res in market.runners}
    if any(resolved[row["selection_id"]] != row["player_id"] for row in market_rows):
        return INCONSISTENT_IDENTITY
    return None


def _as_of_date(start_time: datetime) -> pd.Timestamp:
    """Fecha UTC del partido como marca sin zona, como las fechas del dataset."""
    stamp = pd.Timestamp(start_time)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp.normalize()


def _finite(value: float | None) -> float | None:
    """NaN e infinitos no son JSON valido: se guardan como null."""
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _stored_features(
    result: asof.AsOfFeatures,
    manifest: ModelManifest,
    market: identity_service.MarketIdentity,
    selection_id_a: int,
) -> dict[str, Any]:
    """Features del modelo (orientacion A-B) y la procedencia para poder auditar la fila."""
    stored: dict[str, Any] = {name: _finite(result.values[name]) for name in manifest.feature_names}
    stored["_asof"] = {
        "data_as_of": None if result.data_as_of is None else str(result.data_as_of.date()),
        "attribute_source": result.attribute_source,
        "attribute_age_days": result.attribute_age_days,
        "surface": market.surface,
        "player_a_selection_id": selection_id_a,
        "player_a_id": market.runners[0][2].player_id,
        "player_b_id": market.runners[1][2].player_id,
        "matches_before": [result.player_a_matches_before, result.player_b_matches_before],
    }
    return stored


__all__ = [
    "DATE_BEFORE_DATA",
    "INCONSISTENT_IDENTITY",
    "NO_OBSERVATION",
    "NO_PLAYER_ID",
    "THIN_HISTORY",
    "MarketPrediction",
    "PredictionReport",
    "PredictionRow",
    "predict_markets",
]

"""Predicciones de mercados proximos (Carril A, A3).

El servicio es puro: recibe filas de corredores (como las devuelve
`repositories.markets_for_prediction`), el estado «a fecha de hoy» de A1 y un modelo, y
devuelve filas de `prediction` o el motivo por el que un mercado se excluye. Estos tests fijan
que cada exclusion se CUENTA, que no se predice con identidad dudosa y que las probabilidades
no dependen de que jugador se etiquete como A.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from tests.test_db_integration import NOW, _payload
from tests.test_features_asof import _matches

from edgecourt.cli import build_parser
from edgecourt.db import repositories
from edgecourt.features import asof
from edgecourt.identity import service as identity_service
from edgecourt.identity.players import PlayerIndex
from edgecourt.identity.tournaments import SurfaceIndex
from edgecourt.models.base import ModelManifest, as_probability_frame, feature_hash
from edgecourt.models.logistic import DEFAULT_FEATURES, LogisticModel
from edgecourt.prediction import service

pytest_plugins = ["tests.conftest_db"]

START = datetime(2023, 3, 1, 15, 0, tzinfo=UTC)  # posterior al ultimo partido (2023-02-12)
OBSERVED = datetime(2023, 3, 1, 12, 0, tzinfo=UTC)


def _history() -> pd.DataFrame:
    """Historico sintetico de 5 jugadores (p1..p5) en un unico torneo de pista dura."""
    frame = _matches()
    frame["tourney_name"] = "Shanghai"
    frame["surface"] = "hard"
    return frame


MATCHES = _history()
STATE = asof.build_asof_state(MATCHES)
PLAYER_INDEX = PlayerIndex.build(MATCHES, aliases={})
SURFACE_INDEX = SurfaceIndex.build(MATCHES)


@dataclass
class _EloModel:
    """Modelo de prueba: logistica del Elo, antisimetrica como el real."""

    name: str = "elo_test"
    feature_names: tuple[str, ...] = ("elo_diff",)

    def predict_proba(self, features: pd.DataFrame) -> pd.DataFrame:
        raw = features["elo_diff"].to_numpy(dtype=float) / 100.0
        return as_probability_frame(1.0 / (1.0 + np.exp(-raw)), features.index)


def _manifest(names: tuple[str, ...] = ("elo_diff",)) -> ModelManifest:
    return ModelManifest(
        model_id="tennis-test-20260101T000000Z",
        model_type="test",
        trained_at="2026-01-01T00:00:00+00:00",
        train_first_year=2000,
        train_last_year=2022,
        n_train_rows=100,
        feature_names=list(names),
        feature_hash=feature_hash(list(names)),
        hyperparameters={},
        artifact_sha256="x",
    )


def _market(
    market_id: str = "1.1",
    names: tuple[str, str] = ("P p1", "P p2"),
    *,
    ids: tuple[str | None, str | None] = ("p1", "p2"),
    competition: str = "ATP Shanghai 2023",
    observation_id: int | None = 10,
    start: datetime = START,
    selections: tuple[int, int] = (101, 102),
) -> list[dict]:
    return [
        {
            "market_id": market_id,
            "market_start_time": start,
            "competition_name": competition,
            "selection_id": selection_id,
            "runner_name": name,
            "sort_priority": index + 1,
            "player_id": player_id,
            "observation_id": observation_id,
            "observed_at": None if observation_id is None else OBSERVED,
            "minutes_to_start": 180.0,
        }
        for index, (selection_id, name, player_id) in enumerate(
            zip(selections, names, ids, strict=True)
        )
    ]


def _predict(rows, *, manifest=None, model=None, min_matches=3):
    return service.predict_markets(
        rows,
        state=STATE,
        model=model or _EloModel(),
        manifest=manifest or _manifest(),
        player_index=PLAYER_INDEX,
        surface_index=SURFACE_INDEX,
        min_matches=min_matches,
    )


# --- Prediccion de un mercado listo ----------------------------------------------


@pytest.mark.critical
def test_ready_market_produces_one_row_per_runner_summing_to_one():
    report = _predict(_market())

    assert report.predicted == 1
    assert dict(report.excluded) == {}
    assert [r.selection_id for r in report.rows] == [101, 102]
    assert sum(r.probability for r in report.rows) == pytest.approx(1.0, abs=1e-9)
    assert all(0.0 < r.probability < 1.0 for r in report.rows)
    first = report.rows[0]
    assert first.model_id == "tennis-test-20260101T000000Z"
    assert (first.observation_id, first.observed_at, first.market_id) == (10, OBSERVED, "1.1")
    assert first.feature_hash == feature_hash(["elo_diff"])


def test_stored_features_are_valid_json_and_carry_provenance():
    report = _predict(_market())
    stored = report.rows[0].features

    json.dumps(stored, allow_nan=False)  # NaN o infinito no son JSON valido
    meta = stored["_asof"]
    assert meta["player_a_id"] == "p1" and meta["player_b_id"] == "p2"
    assert meta["player_a_selection_id"] == 101
    assert meta["surface"] == "hard"
    assert meta["data_as_of"] == "2023-02-12"
    assert meta["matches_before"][0] > 0 and meta["matches_before"][1] > 0
    assert stored["elo_diff"] == pytest.approx(
        STATE.elo.overall.rating("p1") - STATE.elo.overall.rating("p2")
    )


@pytest.mark.critical
def test_probability_of_each_player_does_not_depend_on_who_is_labelled_a():
    """Intercambiar el orden de los corredores no cambia la probabilidad de cada jugador."""
    straight = _predict(_market(names=("P p1", "P p2"), ids=("p1", "p2")))
    swapped = _predict(_market(names=("P p2", "P p1"), ids=("p2", "p1")))

    by_player_straight = {"p1": straight.rows[0].probability, "p2": straight.rows[1].probability}
    by_player_swapped = {"p2": swapped.rows[0].probability, "p1": swapped.rows[1].probability}
    for player in ("p1", "p2"):
        assert by_player_straight[player] == pytest.approx(by_player_swapped[player], abs=1e-6)


def test_runner_order_in_input_does_not_matter():
    rows = _market()
    forward = _predict(rows)
    backward = _predict(list(reversed(rows)))

    assert [r.selection_id for r in backward.rows] == [101, 102]
    assert [r.probability for r in backward.rows] == [r.probability for r in forward.rows]


def test_real_logistic_model_runs_on_asof_features():
    names = list(DEFAULT_FEATURES)
    assert tuple(names) == asof.ALL_FEATURE_NAMES
    rng = np.random.default_rng(0)
    train = pd.DataFrame(rng.normal(size=(200, len(names))), columns=names)
    target = (train["elo_diff"] + rng.normal(size=200) > 0).astype(int)
    model = LogisticModel().fit(train, target)

    report = _predict(_market(), model=model, manifest=_manifest(tuple(names)))

    assert report.predicted == 1
    assert sum(r.probability for r in report.rows) == pytest.approx(1.0, abs=1e-9)
    json.dumps(report.rows[0].features, allow_nan=False)


# --- Exclusiones: nunca en silencio ------------------------------------------------


@pytest.mark.critical
@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        (_market(competition="WTA Madrid 2023"), identity_service.OUT_OF_SCOPE),
        (_market(names=("Jugador Desconocido", "P p2")), identity_service.UNRESOLVED_PLAYER),
        (_market(competition="ATP Nuevo 2023"), identity_service.UNRESOLVED_SURFACE),
        (_market(observation_id=None), service.NO_OBSERVATION),
        (_market(ids=(None, "p2")), service.NO_PLAYER_ID),
        (_market(ids=("p3", "p2")), service.INCONSISTENT_IDENTITY),
        (_market(start=datetime(2023, 2, 1, 15, tzinfo=UTC)), service.DATE_BEFORE_DATA),
    ],
)
def test_excluded_markets_are_counted_with_their_reason(rows, reason):
    report = _predict(rows)

    assert report.rows == [] and report.predicted == 0
    assert report.markets_seen == 1
    assert dict(report.excluded) == {reason: 1}


def test_thin_history_is_excluded():
    report = _predict(_market(), min_matches=10_000)

    assert dict(report.excluded) == {service.THIN_HISTORY: 1}
    assert report.rows == []


def test_one_bad_market_does_not_block_the_others():
    rows = (
        _market("1.1")
        + _market("1.2", ids=(None, None))
        + _market("1.3", names=("P p3", "P p4"), ids=("p3", "p4"))
    )
    report = _predict(rows)

    assert report.markets_seen == 3
    assert {m.market_id for m in report.markets} == {"1.1", "1.3"}
    assert dict(report.excluded) == {service.NO_PLAYER_ID: 1}


def test_empty_input_returns_an_empty_report():
    report = _predict([])

    assert (report.markets_seen, report.predicted, dict(report.excluded)) == (0, 0, {})


# --- El modelo debe ser el que dice su manifiesto -----------------------------------


def test_manifest_feature_hash_mismatch_is_rejected():
    broken = dataclasses.replace(_manifest(), feature_hash="deadbeefdeadbeef")
    with pytest.raises(ValueError, match="feature_hash"):
        _predict(_market(), manifest=broken)


def test_features_that_a1_does_not_compute_are_rejected():
    with pytest.raises(ValueError, match="no calcula"):
        _predict(_market(), manifest=_manifest(("elo_diff", "feature_inventada")))


def test_model_with_different_features_than_its_manifest_is_rejected():
    model = _EloModel(feature_names=("surface_elo_diff",))
    with pytest.raises(ValueError, match="difieren"):
        _predict(_market(), model=model)


# --- CLI ---------------------------------------------------------------------------------


def test_predict_and_promote_are_report_only_by_default():
    parser = build_parser()

    predict = parser.parse_args(["predict"])
    assert (predict.apply, predict.horizon_hours, predict.max_data_age_days) == (False, 48.0, 14)

    promote = parser.parse_args(["model", "promote", "tennis-x", "--by", "responsable"])
    assert promote.apply is False and promote.by == "responsable"


def test_promote_requires_a_responsible_person():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["model", "promote", "tennis-x"])


# --- Persistencia (PostgreSQL de pruebas) ---------------------------------------------


def _seed_market(db, *, start: datetime, player_ids=("p1", "p2")) -> None:
    with db.transaction(), db.cursor() as cursor:
        repositories.upsert_event(
            cursor,
            {
                "event_id": "ev1",
                "event_name": "P p1 v P p2",
                "competition_id": None,
                "competition_name": "ATP Shanghai 2026",
                "country_code": "ES",
                "timezone": "GMT",
                "open_date": start,
            },
        )
        repositories.upsert_market(
            cursor,
            {
                "market_id": "1.001",
                "event_id": "ev1",
                "market_name": "Match Odds",
                "market_type": "MATCH_ODDS",
                "market_start_time": start,
            },
        )
        for (selection_id, name), player_id in zip(
            ((101, "P p1"), (102, "P p2")), player_ids, strict=True
        ):
            repositories.upsert_runner(
                cursor,
                {
                    "market_id": "1.001",
                    "selection_id": selection_id,
                    "runner_name": name,
                    "sort_priority": 1,
                    "handicap": 0,
                },
            )
            if player_id:
                cursor.execute(
                    "UPDATE betfair_runner SET player_id = %s WHERE selection_id = %s",
                    (player_id, selection_id),
                )
        repositories.ensure_partitions(cursor, NOW)


def _observe(db) -> None:
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=True))


@pytest.mark.integration
def test_markets_for_prediction_returns_runners_with_latest_observation(db):
    start = NOW + timedelta(hours=6)
    _seed_market(db, start=start)
    _observe(db)

    with db.transaction(), db.cursor() as cursor:
        rows = repositories.markets_for_prediction(
            cursor, now=NOW + timedelta(minutes=5), horizon_hours=48
        )

    assert [(r["selection_id"], r["runner_name"], r["player_id"]) for r in rows] == [
        (101, "P p1", "p1"),
        (102, "P p2", "p2"),
    ]
    assert all(r["observation_id"] is not None for r in rows)
    assert all(r["competition_name"] == "ATP Shanghai 2026" for r in rows)


@pytest.mark.integration
def test_markets_for_prediction_respects_horizon_start_and_observation_age(db):
    start = NOW + timedelta(hours=6)
    _seed_market(db, start=start)
    _observe(db)

    def query(**kwargs):
        with db.transaction(), db.cursor() as cursor:
            return repositories.markets_for_prediction(cursor, **kwargs)

    soon = NOW + timedelta(minutes=5)
    assert query(now=soon, horizon_hours=1) == []  # empieza fuera del horizonte
    assert query(now=start + timedelta(minutes=1), horizon_hours=48) == []  # ya empezo
    stale = query(now=soon, horizon_hours=48, max_observation_age_hours=0.05)  # 3 min < 5 min
    assert len(stale) == 2 and all(r["observation_id"] is None for r in stale)


@pytest.mark.integration
@pytest.mark.critical
def test_promote_model_keeps_a_single_production_and_records_who(db):
    first = _manifest()
    second = dataclasses.replace(first, model_id="tennis-test-20260201T000000Z")

    with db.transaction(), db.cursor() as cursor:
        assert repositories.production_model(cursor) is None
        assert (
            repositories.promote_model(cursor, first, artifact_path="p/1", promoted_by="ana")
            is None
        )
    with db.transaction(), db.cursor() as cursor:
        retired = repositories.promote_model(
            cursor, second, artifact_path="p/2", promoted_by="beto"
        )
        current = repositories.production_model(cursor)
        cursor.execute("SELECT model_id, slot FROM model_version ORDER BY model_id")
        slots = {r["model_id"]: r["slot"] for r in cursor.fetchall()}

    assert retired == first.model_id
    assert (current["model_id"], current["promoted_by"]) == (second.model_id, "beto")
    assert current["promoted_at"] is not None
    assert slots == {first.model_id: "retired", second.model_id: "production"}


@pytest.mark.integration
def test_promoting_the_model_already_in_production_is_refused(db):
    manifest = _manifest()
    with db.transaction(), db.cursor() as cursor:
        repositories.promote_model(cursor, manifest, artifact_path="p", promoted_by="ana")
    with (
        pytest.raises(ValueError, match="ya esta en production"),
        db.transaction(),
        db.cursor() as cursor,
    ):
        repositories.promote_model(cursor, manifest, artifact_path="p", promoted_by="ana")


@pytest.mark.integration
@pytest.mark.critical
def test_database_to_prediction_rows_end_to_end_and_idempotent(db):
    _seed_market(db, start=NOW + timedelta(hours=6))
    _observe(db)
    manifest = _manifest()

    with db.transaction(), db.cursor() as cursor:
        rows = repositories.markets_for_prediction(
            cursor, now=NOW + timedelta(minutes=5), horizon_hours=48
        )
    report = _predict(rows, manifest=manifest)
    assert report.predicted == 1 and not report.excluded

    with db.transaction(), db.cursor() as cursor:
        repositories.promote_model(cursor, manifest, artifact_path="p", promoted_by="ana")
        first_run = [repositories.insert_prediction(cursor, r) for r in report.rows]
        second_run = [repositories.insert_prediction(cursor, r) for r in report.rows]
        cursor.execute(
            "SELECT count(*) AS n, sum(probability) AS total, min(features->>'elo_diff') AS elo "
            "FROM prediction"
        )
        stored = cursor.fetchone()

    assert first_run == [True, True] and second_run == [False, False]
    assert stored["n"] == 2
    assert float(stored["total"]) == pytest.approx(1.0, abs=1e-9)
    assert stored["elo"] is not None


@pytest.mark.integration
def test_prediction_requires_a_registered_model(db):
    import psycopg

    _seed_market(db, start=NOW + timedelta(hours=6))
    _observe(db)
    with db.transaction(), db.cursor() as cursor:
        rows = repositories.markets_for_prediction(
            cursor, now=NOW + timedelta(minutes=5), horizon_hours=48
        )
    report = _predict(rows)

    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        db.transaction(),
        db.cursor() as cursor,
    ):
        repositories.insert_prediction(cursor, report.rows[0])  # model_version vacio

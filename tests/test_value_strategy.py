"""Estrategia v1 del Carril A (A4): regla de valor, candidatos y consulta de decision.

La regla esta CONGELADA: estos tests fijan sus umbrales y que no se leen de la configuracion.
Los numeros esperados se calculan aqui con las formulas del documento de estrategia, no con el
codigo bajo prueba.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from tests.test_db_integration import NOW, _payload
from tests.test_prediction import _manifest, _seed_market

from edgecourt.cli import build_parser
from edgecourt.config import PROJECT_ROOT, Settings
from edgecourt.db import repositories
from edgecourt.prediction.service import PredictionRow
from edgecourt.risk import limits as risk
from edgecourt.risk.limits import RiskLimits
from edgecourt.value import candidates as pipeline
from edgecourt.value import strategy as rules
from edgecourt.value.strategy import MarketBook, RunnerBook, StrategyV1

pytest_plugins = ["tests.conftest_db"]

STRATEGY = StrategyV1()
LIMITS = RiskLimits(
    bankroll=1000.0,
    max_stake_fraction=0.01,
    max_market_exposure=0.02,
    max_daily_exposure=0.05,
    max_drawdown=0.20,
)
OBSERVED = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def _book(
    probabilities=(0.56, 0.44),
    prices=(1.95, 1.95),
    sizes=(250.0, 250.0),
    *,
    total_available=500.0,
    market_id="1.1",
    observed_at=OBSERVED,
) -> MarketBook:
    first, second = (
        RunnerBook(
            selection_id=101 + i,
            name=f"Jugador {i}",
            back_price=price,
            back_size=size,
            model_probability=probability,
        )
        for i, (probability, price, size) in enumerate(
            zip(probabilities, prices, sizes, strict=True)
        )
    )
    return MarketBook(
        market_id=market_id,
        observation_id=10,
        observed_at=observed_at,
        minutes_to_start=45.0,
        total_available=total_available,
        runners=(first, second),
    )


# --- La estrategia esta congelada -----------------------------------------------------------


@pytest.mark.critical
def test_strategy_v1_document_still_matches_its_frozen_hash():
    document = PROJECT_ROOT / "docs" / "phases" / "PHASE_A_STRATEGY_V1_PROPOSAL.md"

    assert hashlib.sha256(document.read_bytes()).hexdigest() == rules.STRATEGY_V1_SHA256


def test_v1_thresholds_are_the_frozen_ones():
    assert (STRATEGY.min_edge, STRATEGY.min_ev_after_costs, STRATEGY.max_edge) == (0.03, 0.03, 0.15)
    assert STRATEGY.stake_fraction == 0.01
    assert (STRATEGY.window_min_minutes, STRATEGY.window_max_minutes) == (20.0, 60.0)


@pytest.mark.critical
def test_thresholds_are_not_read_from_the_configuration():
    settings = Settings(
        _env_file=None, minimum_edge=0.10, betfair_commission=0.04, minimum_liquidity=80.0
    )
    strategy = StrategyV1.from_settings(settings)

    assert strategy.min_edge == 0.03  # no se mueve con MINIMUM_EDGE
    assert strategy.commission == 0.04  # supuesto corregible: si viene de la configuracion
    assert strategy.min_market_liquidity == 80.0  # DEC-012


# --- Regla de valor ---------------------------------------------------------------------------


def test_a_clear_edge_is_a_candidate_with_the_documented_numbers():
    evaluation = rules.evaluate_market(_book(), STRATEGY)
    chosen = evaluation.chosen

    assert evaluation.status == rules.CANDIDATE and evaluation.reason is None
    assert chosen.selection_id == 101
    assert chosen.market_probability_devig == pytest.approx(0.5)  # libro simetrico
    assert chosen.edge == pytest.approx(0.06)
    assert chosen.expected_value == pytest.approx(0.56 * 1.95 - 1)
    assert chosen.expected_value_after_costs == pytest.approx(0.56 * (1.95 - 1) * 0.95 - 0.44)
    # Un tick peor (1.94) baja el EV: la variante pesimista siempre es menor.
    assert chosen.expected_value_after_costs_pessimistic == pytest.approx(
        0.56 * (1.94 - 1) * 0.95 - 0.44
    )
    assert chosen.expected_value_after_costs_pessimistic < chosen.expected_value_after_costs


@pytest.mark.parametrize(
    ("probabilities", "status", "reason"),
    [
        ((0.51, 0.49), rules.EXCLUDED, rules.BELOW_EDGE),  # edge 1 pp
        ((0.535, 0.465), rules.EXCLUDED, rules.BELOW_EV),  # edge 3,5 pp pero EV neto 1,8 %
        ((0.70, 0.30), rules.ANOMALY, rules.ANOMALOUS_EDGE),  # edge 20 pp > 15 pp
    ],
)
def test_thresholds_split_candidates_exclusions_and_anomalies(probabilities, status, reason):
    evaluation = rules.evaluate_market(_book(probabilities), STRATEGY)

    assert (evaluation.status, evaluation.reason) == (status, reason)
    assert evaluation.chosen is not None and evaluation.stake == 0.0


def test_the_runner_with_the_higher_net_ev_is_the_one_chosen():
    evaluation = rules.evaluate_market(_book((0.44, 0.56)), STRATEGY)

    assert evaluation.status == rules.CANDIDATE
    assert evaluation.chosen.selection_id == 102
    assert [r.selection_id for r in evaluation.runners] == [101, 102]


def test_at_most_one_runner_per_market_is_chosen():
    evaluation = rules.evaluate_market(_book(), STRATEGY)

    assert evaluation.chosen in evaluation.runners
    assert sum(r is evaluation.chosen for r in evaluation.runners) == 1


@pytest.mark.parametrize(
    ("book", "reason"),
    [
        (_book((None, 0.44)), rules.NO_PREDICTION),
        (_book((0.0, 1.0)), rules.NO_PREDICTION),
        (_book((0.56, 0.60)), rules.INCONSISTENT_PREDICTION),
        (_book(prices=(None, 1.95)), rules.NO_BACK_PRICES),
        (_book(prices=(1.95, 1.0)), rules.NO_BACK_PRICES),
        (_book(sizes=(250.0, 0.0)), rules.NO_BACK_PRICES),
        (_book(total_available=10.0), rules.LOW_MARKET_LIQUIDITY),
        (_book(prices=(2.0, 2.0)), rules.INCOHERENT_BOOK),  # sin sobre-redondo
        (_book(prices=(1.90, 2.30)), rules.INCOHERENT_BOOK),
    ],
)
def test_unusable_books_are_excluded_with_their_reason(book, reason):
    evaluation = rules.evaluate_market(book, STRATEGY)

    assert (evaluation.status, evaluation.reason) == (rules.EXCLUDED, reason)
    assert evaluation.chosen is None


@pytest.mark.critical
def test_commission_changes_the_decision_but_not_the_thresholds():
    book = _book((0.545, 0.455))  # edge 4,5 pp

    cheap = rules.evaluate_market(book, dataclasses.replace(STRATEGY, commission=0.0))
    dear = rules.evaluate_market(book, dataclasses.replace(STRATEGY, commission=0.10))

    assert cheap.status == rules.CANDIDATE
    assert (dear.status, dear.reason) == (rules.EXCLUDED, rules.BELOW_EV)


def test_decide_market_sizes_the_stake_with_the_risk_limits():
    decided = rules.decide_market(_book(), STRATEGY, LIMITS)

    assert decided.status == rules.CANDIDATE and decided.stake == 10.0


def test_decide_market_caps_the_stake_to_the_visible_size():
    decided = rules.decide_market(_book(sizes=(4.0, 250.0)), STRATEGY, LIMITS)

    assert decided.status == rules.CANDIDATE and decided.stake == 4.0


def test_a_candidate_without_enough_depth_is_excluded():
    decided = rules.decide_market(_book(sizes=(1.5, 250.0)), STRATEGY, LIMITS)

    assert (decided.status, decided.reason, decided.stake) == (
        rules.EXCLUDED,
        risk.VISIBLE_SIZE,
        0.0,
    )


def test_drawdown_limit_halts_new_bets():
    decided = rules.decide_market(_book(), STRATEGY, LIMITS, drawdown=0.20)

    assert (decided.status, decided.reason) == (rules.EXCLUDED, risk.DRAWDOWN_LIMIT)


# --- Candidatos sobre varios mercados ---------------------------------------------------------


def _rows(
    market_id="1.1",
    *,
    probabilities=(0.56, 0.44),
    prices=(1.95, 1.95),
    observation_id=10,
    observed_at=OBSERVED,
    total_available=500.0,
    runners=2,
):
    rows = []
    for index in range(runners):
        rows.append(
            {
                "market_id": market_id,
                "selection_id": 101 + index,
                "runner_name": f"Jugador {index}",
                "observation_id": observation_id,
                "observed_at": None if observation_id is None else observed_at,
                "minutes_to_start": 45.0,
                "total_available": total_available,
                "back_price": None if observation_id is None else prices[index],
                "back_size": None if observation_id is None else 250.0,
                "probability": probabilities[index],
            }
        )
    return rows


def _run(rows, **kwargs):
    return pipeline.find_candidates(rows, strategy=STRATEGY, limits=LIMITS, **kwargs)


def test_markets_are_classified_and_never_dropped_silently():
    rows = (
        _rows("1.1")
        + _rows("1.2", observation_id=None)  # ventana de decision todavia no abierta
        + _rows("1.3", runners=1)  # forma inesperada
        + _rows("1.4", probabilities=(None, None))  # sin prediccion
        + _rows("1.5", probabilities=(0.70, 0.30))  # anomalia
        + _rows("1.6", probabilities=(0.51, 0.49))  # edge insuficiente
    )
    report = _run(rows)

    assert report.markets_seen == 6
    assert [c.market_id for c in report.candidates] == ["1.1"]
    assert [a.market_id for a in report.anomalies] == ["1.5"]
    assert dict(report.excluded) == {
        pipeline.WINDOW_PENDING: 1,
        pipeline.UNEXPECTED_SHAPE: 1,
        rules.NO_PREDICTION: 1,
        rules.BELOW_EDGE: 1,
    }
    assert report.total_stake == 10.0


def test_database_numeric_types_are_accepted():
    rows = _rows()
    for row in rows:
        for key in (
            "back_price",
            "back_size",
            "probability",
            "minutes_to_start",
            "total_available",
        ):
            row[key] = Decimal(str(row[key]))

    report = _run(rows)

    assert len(report.candidates) == 1 and report.candidates[0].stake == 10.0


def _same_day_markets(count, *, start=OBSERVED, day_offset=0):
    rows = []
    for index in range(count):
        moment = start + timedelta(days=day_offset, minutes=index)
        rows += _rows(f"1.{day_offset}{index}", observed_at=moment)
    return rows


@pytest.mark.critical
def test_daily_exposure_limit_is_respected_within_one_pass_in_chronological_order():
    rows = list(reversed(_same_day_markets(6)))  # el orden de entrada no importa
    report = _run(rows)

    assert [c.market_id for c in report.candidates] == [f"1.0{i}" for i in range(5)]
    assert all(c.stake == 10.0 for c in report.candidates)
    assert report.total_stake == 50.0  # 5 % del bankroll inicial
    assert dict(report.excluded) == {risk.DAILY_LIMIT: 1}


def test_the_daily_limit_resets_on_the_next_utc_day():
    rows = _same_day_markets(6) + _same_day_markets(2, day_offset=1)
    report = _run(rows)

    assert len(report.candidates) == 7
    assert dict(report.excluded) == {risk.DAILY_LIMIT: 1}


def test_previous_exposure_of_the_day_reduces_the_stake():
    report = _run(_same_day_markets(2), exposure_by_day={date(2026, 11, 4): 45.0})

    assert [c.stake for c in report.candidates] == [5.0]
    assert dict(report.excluded) == {risk.DAILY_LIMIT: 1}


def test_drawdown_halts_every_market():
    report = _run(_same_day_markets(3), drawdown=0.25)

    assert report.candidates == []
    assert dict(report.excluded) == {risk.DRAWDOWN_LIMIT: 3}


def test_no_markets_gives_an_empty_report():
    report = _run([])

    assert (report.markets_seen, report.candidates, report.anomalies) == (0, [], [])
    assert report.total_stake == 0.0


# --- CLI ---------------------------------------------------------------------------------------


def test_paper_candidates_is_read_only_by_construction_and_status_stays_reserved():
    parser = build_parser()

    candidates = parser.parse_args(["paper", "candidates"])
    assert (candidates.command, candidates.subcommand, candidates.horizon_hours) == (
        "paper",
        "candidates",
        48.0,
    )
    assert not hasattr(candidates, "apply")  # no existe una variante que escriba
    assert parser.parse_args(["paper", "status"]).subcommand == "status"


# --- Consulta de decision (PostgreSQL de pruebas) -------------------------------------------


def _observe_at(
    db, *, capture_key, observed_at, minutes, prices=(1.95, 1.95), sizes=(250.0, 250.0)
):
    payload = _payload(with_prices=True, capture_key=capture_key)
    runners = [dict(runner) for runner in payload.runners]
    for runner, price, size in zip(runners, prices, sizes, strict=True):
        runner["back_price_1"] = price
        runner["back_size_1"] = size
        runner["lay_price_1"] = round(price + 0.02, 2)
    payload = dataclasses.replace(
        payload, observed_at=observed_at, minutes_to_start=minutes, runners=runners
    )
    with db.transaction(), db.cursor() as cursor:
        return repositories.save_observation(cursor, payload)


def _rows_from_db(db, *, model_id="m", now=None):
    with db.transaction(), db.cursor() as cursor:
        return repositories.candidate_rows(
            cursor,
            model_id=model_id,
            now=now or NOW + timedelta(minutes=11),
            horizon_hours=48,
            window_min_minutes=20,
            window_max_minutes=60,
        )


@pytest.mark.integration
@pytest.mark.critical
def test_the_decision_observation_is_the_first_priced_one_inside_the_window(db):
    _seed_market(db, start=NOW + timedelta(minutes=45))
    _observe_at(
        db,
        capture_key="t70",
        observed_at=NOW - timedelta(minutes=25),
        minutes=70.0,
        prices=(1.50, 2.80),
    )  # fuera de la ventana: mas de 60 min
    first = _observe_at(db, capture_key="t45", observed_at=NOW, minutes=45.0)
    _observe_at(
        db,
        capture_key="t35",
        observed_at=NOW + timedelta(minutes=10),
        minutes=35.0,
        prices=(1.60, 2.60),
    )  # dentro de la ventana, pero posterior

    rows = _rows_from_db(db)

    assert [r["observation_id"] for r in rows] == [first, first]
    assert [float(r["back_price"]) for r in rows] == [1.95, 1.95]
    assert [float(r["back_size"]) for r in rows] == [250.0, 250.0]
    assert all(r["probability"] is None for r in rows)  # aun no hay prediccion


@pytest.mark.integration
def test_a_market_without_a_decision_observation_yet_is_listed_with_nulls(db):
    _seed_market(db, start=NOW + timedelta(minutes=45))
    _observe_at(db, capture_key="t45", observed_at=NOW, minutes=45.0)

    rows = _rows_from_db(db, now=NOW - timedelta(minutes=30))  # la observacion aun no existe

    assert len(rows) == 2 and all(r["observation_id"] is None for r in rows)
    assert pipeline.find_candidates(rows, strategy=STRATEGY, limits=LIMITS).excluded == {
        pipeline.WINDOW_PENDING: 1
    }


@pytest.mark.integration
def test_started_or_distant_markets_are_not_listed(db):
    _seed_market(db, start=NOW + timedelta(minutes=45))
    _observe_at(db, capture_key="t45", observed_at=NOW, minutes=45.0)

    assert _rows_from_db(db, now=NOW + timedelta(minutes=46)) == []  # ya empezo
    far = _rows_from_db(db, now=NOW - timedelta(hours=60))
    assert far == []  # fuera del horizonte de 48 h


@pytest.mark.integration
@pytest.mark.critical
def test_database_to_candidate_end_to_end(db):
    _seed_market(db, start=NOW + timedelta(minutes=45))
    observation = _observe_at(db, capture_key="t45", observed_at=NOW, minutes=45.0)
    manifest = _manifest()
    with db.transaction(), db.cursor() as cursor:
        repositories.promote_model(cursor, manifest, artifact_path="p", promoted_by="ana")
        for selection_id, probability in ((101, 0.56), (102, 0.44)):
            repositories.insert_prediction(
                cursor,
                PredictionRow(
                    model_id=manifest.model_id,
                    observation_id=observation,
                    observed_at=NOW,
                    market_id="1.001",
                    selection_id=selection_id,
                    probability=probability,
                    feature_hash=manifest.feature_hash,
                    features={},
                ),
            )

    rows = _rows_from_db(db, model_id=manifest.model_id)
    report = _run(rows)

    assert [float(r["probability"]) for r in rows] == [0.56, 0.44]
    assert report.markets_seen == 1 and not report.excluded and not report.anomalies
    (candidate,) = report.candidates
    assert (candidate.market_id, candidate.chosen.selection_id, candidate.stake) == (
        "1.001",
        101,
        10.0,
    )
    assert candidate.chosen.edge == pytest.approx(0.06)


@pytest.mark.integration
def test_predictions_of_other_models_are_ignored(db):
    _seed_market(db, start=NOW + timedelta(minutes=45))
    observation = _observe_at(db, capture_key="t45", observed_at=NOW, minutes=45.0)
    other = dataclasses.replace(_manifest(), model_id="tennis-otro-20260101T000000Z")
    with db.transaction(), db.cursor() as cursor:
        repositories.promote_model(cursor, other, artifact_path="p", promoted_by="ana")
        repositories.insert_prediction(
            cursor,
            PredictionRow(
                other.model_id, observation, NOW, "1.001", 101, 0.9, other.feature_hash, {}
            ),
        )

    rows = _rows_from_db(db, model_id="tennis-test-20260101T000000Z")

    assert all(r["probability"] is None for r in rows)

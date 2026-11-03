"""Precio de cierre por mercado (Phase 3.5-B, DEC-021): vista `market_closing_observation`.

La definicion se fijo antes de ver resultados: ultima observacion pre-play con
precios anterior a la primera senal de juego, fiable si esta a <= 5 min de la
referencia de inicio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from edgecourt.db import repositories

pytest_plugins = ["tests.conftest_db"]

START = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
RUN_ID = uuid.UUID("00000000-0000-0000-0000-00000000c105")


def _seed(db, market_id: str = "1.001", start: datetime = START) -> None:
    with db.transaction(), db.cursor() as cursor:
        repositories.ensure_partitions(cursor, START)
        repositories.upsert_event(
            cursor,
            {
                "event_id": f"ev-{market_id}",
                "event_name": "A v B",
                "competition_id": None,
                "competition_name": "ATP",
                "country_code": "ES",
                "timezone": "GMT",
                "open_date": start,
            },
        )
        repositories.upsert_market(
            cursor,
            {
                "market_id": market_id,
                "event_id": f"ev-{market_id}",
                "market_name": "Match Odds",
                "market_type": "MATCH_ODDS",
                "market_start_time": start,
            },
        )
        for selection_id, name in ((101, "A"), (102, "B")):
            repositories.upsert_runner(
                cursor,
                {
                    "market_id": market_id,
                    "selection_id": selection_id,
                    "runner_name": name,
                    "sort_priority": 1,
                    "handicap": 0,
                },
            )


def _set_start(db, market_id: str, start: datetime) -> None:
    """Simula un aplazamiento posterior a la ultima observacion."""
    with db.transaction(), db.cursor() as cursor:
        cursor.execute(
            "UPDATE betfair_market SET market_start_time = %s WHERE market_id = %s",
            (start, market_id),
        )


def _obs(
    db,
    minutes_before: float,
    *,
    market_id: str = "1.001",
    priced: bool = True,
    inplay: bool = False,
    status: str = "OPEN",
    label: str = "adaptive",
    key: str | None = None,
    start: datetime = START,
) -> int:
    runners = []
    for selection_id in (101, 102):
        runner = {
            "selection_id": selection_id,
            "runner_status": "ACTIVE",
            "last_price_traded": None,
            "runner_total_matched": None,
        }
        for column in repositories.PRICE_COLUMNS:
            runner[column] = None
        if priced:
            runner.update(back_price_1=1.72, back_size_1=250.0, lay_price_1=1.76, lay_size_1=180.0)
        runners.append(runner)
    observed_at = start - timedelta(minutes=minutes_before)
    payload = repositories.ObservationPayload(
        market_id=market_id,
        observed_at=observed_at,
        capture_key=key or f"k{minutes_before}",
        snapshot_label=label,
        minutes_to_start=minutes_before,
        market_status=status,
        inplay=inplay,
        bet_delay=0,
        active_runners=2,
        total_matched=0.0,
        collector_run_id=RUN_ID,
        runners=runners,
    )
    with db.transaction(), db.cursor() as cursor:
        return repositories.save_observation(cursor, payload)


def _closing(db, market_id: str = "1.001") -> dict:
    with db.cursor() as cursor:
        rows = [r for r in repositories.closing_observations(cursor) if r["market_id"] == market_id]
    db.commit()
    assert len(rows) == 1
    return rows[0]


# --- Casos de la definicion -----------------------------------------------------


@pytest.mark.integration
@pytest.mark.critical
def test_on_time_market_closes_at_last_priced_observation(db):
    _seed(db)
    _obs(db, 30)
    last = _obs(db, 3)
    row = _closing(db)
    assert row["closing_observation_id"] == last
    assert row["reference_kind"] == "published_start"
    assert float(row["close_gap_min"]) == 3.0
    assert row["close_reliable"] is True
    assert row["no_close_reason"] is None


@pytest.mark.integration
@pytest.mark.critical
def test_inplay_before_published_start_uses_last_prematch_priced_observation(db):
    _seed(db)
    _obs(db, 40)
    pre = _obs(db, 26)
    _obs(db, 25, inplay=True)  # el juego empezo 25 min antes de la hora publicada
    _obs(db, 24, inplay=True)
    row = _closing(db)
    assert row["closing_observation_id"] == pre
    assert row["reference_kind"] == "inplay_observed"
    assert float(row["close_gap_min"]) == 1.0
    assert row["close_reliable"] is True


@pytest.mark.integration
@pytest.mark.critical
def test_postponement_after_last_observation_is_unreliable(db):
    _seed(db)
    last = _obs(db, 3)
    _set_start(db, "1.001", START + timedelta(hours=1))  # inicio aplazado despues
    row = _closing(db)
    assert row["closing_observation_id"] == last
    assert float(row["close_gap_min"]) == 63.0
    assert row["close_reliable"] is False


@pytest.mark.integration
def test_book_that_empties_before_start_keeps_the_last_priced_observation(db):
    _seed(db)
    priced = _obs(db, 50)
    for minutes in (40, 20, 5, 1):
        _obs(db, minutes, priced=False)
    row = _closing(db)
    assert row["closing_observation_id"] == priced
    assert float(row["close_gap_min"]) == 50.0
    assert row["close_reliable"] is False


@pytest.mark.integration
@pytest.mark.critical
def test_market_without_priced_observation_has_no_close(db):
    _seed(db)
    _obs(db, 20, priced=False)
    _obs(db, 2, priced=False)
    row = _closing(db)
    assert row["closing_observation_id"] is None
    assert row["close_reliable"] is None
    assert row["no_close_reason"] == "no_priced_prematch_observation"


@pytest.mark.integration
def test_suspended_or_closed_observations_are_never_the_close(db):
    _seed(db)
    open_obs = _obs(db, 10)
    _obs(db, 4, status="SUSPENDED")
    _obs(db, 2, status="CLOSED")
    row = _closing(db)
    assert row["closing_observation_id"] == open_obs
    assert row["reference_kind"] == "published_start"


@pytest.mark.integration
def test_priced_observation_after_suspension_is_not_a_prematch_close(db):
    _seed(db)
    before = _obs(db, 10)
    _obs(db, 5, status="SUSPENDED")
    _obs(db, 3)  # reapertura posterior: ya no es pre-play fiable
    assert _closing(db)["closing_observation_id"] == before


@pytest.mark.integration
def test_close_label_is_not_used_as_the_close(db):
    _seed(db)
    _obs(db, 20, label="close")
    later = _obs(db, 2, label="adaptive")
    row = _closing(db)
    assert row["closing_observation_id"] == later
    assert row["closing_snapshot_label"] == "adaptive"


@pytest.mark.integration
def test_same_instant_is_resolved_by_observation_id(db):
    _seed(db)
    _obs(db, 5, key="a")
    second = _obs(db, 5, key="b")
    assert _closing(db)["closing_observation_id"] == second


@pytest.mark.integration
def test_reliability_threshold_is_five_minutes_inclusive(db):
    _seed(db, "1.001")
    _obs(db, 5, market_id="1.001")
    _seed(db, "1.002")
    _obs(db, 5.01, market_id="1.002")
    assert _closing(db, "1.001")["close_reliable"] is True
    assert _closing(db, "1.002")["close_reliable"] is False


# --- Consulta, filtros y seguridad ----------------------------------------------


@pytest.mark.integration
def test_summary_and_started_before_filter(db):
    future = START + timedelta(days=30)
    _seed(db, "1.001")
    _obs(db, 2, market_id="1.001")
    _seed(db, "1.002")
    _obs(db, 40, market_id="1.002")
    _seed(db, "1.003")
    _obs(db, 10, market_id="1.003", priced=False)
    _seed(db, "1.004", start=future)  # aun no empieza
    _obs(db, 90, market_id="1.004", start=future)

    with db.cursor() as cursor:
        everything = repositories.closing_summary(cursor)
        finished = repositories.closing_summary(cursor, started_before=START + timedelta(days=1))
        rows = repositories.closing_observations(cursor, started_before=START + timedelta(days=1))
    db.commit()

    assert everything == {"markets": 4, "reliable": 1, "unreliable": 2, "no_close": 1}
    assert finished == {"markets": 3, "reliable": 1, "unreliable": 1, "no_close": 1}
    assert [r["market_id"] for r in rows] == ["1.001", "1.002", "1.003"]


@pytest.mark.integration
def test_view_is_read_only_and_does_not_change_data(db):
    _seed(db)
    _obs(db, 3)
    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) AS n FROM market_observation")
        before = cursor.fetchone()["n"]
    db.commit()

    db.read_only = True
    try:
        row = _closing(db)
    finally:
        db.read_only = False
    assert row["close_reliable"] is True

    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) AS n FROM market_observation")
        assert cursor.fetchone()["n"] == before
    db.commit()


@pytest.mark.integration
def test_result_does_not_depend_on_session_time_zone(db):
    _seed(db)
    _obs(db, 3)
    db.commit()
    with db.cursor() as cursor:
        cursor.execute("SET TIME ZONE 'Europe/Madrid'")
    db.commit()
    row = _closing(db)
    assert float(row["close_gap_min"]) == 3.0
    assert row["closing_observed_at"] == START - timedelta(minutes=3)

"""Tests de integracion contra PostgreSQL real.

Se omiten si no hay `EDGECOURT_TEST_DSN`. Comprueban lo que no se puede
verificar leyendo el DDL: que las restricciones se cumplen de verdad, que las
escrituras son idempotentes y que el ledger es realmente inmutable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from edgecourt.db import repositories
from edgecourt.db.migrate import current_version, discover, migrate, pending

pytestmark = pytest.mark.integration

pytest_plugins = ["tests.conftest_db"]

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
RUN_ID = uuid.uuid4()


def _seed_catalogue(db, market_id="1.001", start=NOW + timedelta(hours=6)):
    with db.transaction(), db.cursor() as cursor:
        repositories.upsert_event(
            cursor,
            {
                "event_id": "ev1",
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
                "event_id": "ev1",
                "market_name": "Match Odds",
                "market_type": "MATCH_ODDS",
                "market_start_time": start,
            },
        )
        for selection_id, name in ((101, "Jugador A"), (102, "Jugador B")):
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
        repositories.ensure_partitions(cursor, NOW)


def _payload(market_id="1.001", *, with_prices: bool, capture_key="6h"):
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
        if with_prices:
            runner["back_price_1"] = 1.72
            runner["back_size_1"] = 250.0
            runner["lay_price_1"] = 1.76
            runner["lay_size_1"] = 180.0
        runners.append(runner)

    return repositories.ObservationPayload(
        market_id=market_id,
        observed_at=NOW,
        capture_key=capture_key,
        snapshot_label=capture_key,
        minutes_to_start=360.0,
        market_status="OPEN",
        inplay=False,
        bet_delay=0,
        active_runners=2,
        total_matched=0.0,
        collector_run_id=RUN_ID,
        runners=runners,
    )


# --- Migraciones --------------------------------------------------------------


def test_migrations_apply_cleanly(db):
    assert current_version(db) == len(discover())
    assert pending(db, discover()) == []


def test_migrations_are_idempotent(db):
    assert migrate(db) == []


# --- Observacion sin precios: el caso que motivo el diseno --------------------


@pytest.mark.critical
def test_observation_without_prices_is_stored(db):
    """Un mercado OPEN sin BACK/LAY debe guardarse, no descartarse."""
    _seed_catalogue(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=False))

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM market_observation")
        row = cursor.fetchone()
        assert row["has_prices"] is False
        assert row["has_liquidity"] is False
        assert row["runners_with_prices"] == 0
        assert float(row["total_available"]) == 0.0

        cursor.execute("SELECT count(*) AS n FROM runner_price")
        assert cursor.fetchone()["n"] == 0, "sin precios no debe haber filas de precio"


def test_observation_with_prices_stores_both(db):
    _seed_catalogue(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=True))

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM market_observation")
        row = cursor.fetchone()
        assert row["has_prices"] is True
        assert row["has_liquidity"] is True
        assert row["runners_with_prices"] == 2
        assert float(row["total_available"]) == pytest.approx(860.0)

        cursor.execute("SELECT count(*) AS n FROM runner_price")
        assert cursor.fetchone()["n"] == 2


# --- Idempotencia -------------------------------------------------------------


@pytest.mark.critical
def test_repeated_save_does_not_duplicate(db):
    _seed_catalogue(db)
    for _ in range(3):
        with db.transaction(), db.cursor() as cursor:
            repositories.save_observation(cursor, _payload(with_prices=True))

    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) AS n FROM market_observation")
        assert cursor.fetchone()["n"] == 1
        cursor.execute("SELECT count(*) AS n FROM runner_price")
        assert cursor.fetchone()["n"] == 2


@pytest.mark.critical
def test_retry_upgrades_an_empty_capture(db):
    """Si un reintento trae precios donde antes no habia, deben prevalecer."""
    _seed_catalogue(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=False))
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=True))

    with db.cursor() as cursor:
        cursor.execute("SELECT has_prices, has_liquidity FROM market_observation")
        row = cursor.fetchone()
        assert row["has_prices"] is True
        assert row["has_liquidity"] is True
        cursor.execute("SELECT count(*) AS n FROM runner_price")
        assert cursor.fetchone()["n"] == 2


# --- Restricciones ------------------------------------------------------------


@pytest.mark.critical
def test_liquidity_without_prices_is_rejected(db):
    """La base de datos impide un estado incoherente."""
    import psycopg

    _seed_catalogue(db)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction(), db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO market_observation (
                market_id, observed_at, capture_key, snapshot_label, minutes_to_start,
                market_status, inplay, has_prices, has_liquidity, collector_run_id
            ) VALUES ('1.001', %s, 'x', 'x', 1, 'OPEN', false, false, true, %s)
            """,
            (NOW, RUN_ID),
        )


@pytest.mark.critical
def test_only_one_production_model(db):
    import psycopg

    def insert(model_id, slot):
        with db.transaction(), db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO model_version (
                    model_id, model_type, slot, trained_at, train_first_year,
                    train_last_year, n_train_rows, feature_names, feature_hash,
                    artifact_sha256, artifact_path
                ) VALUES (%s, 'logreg', %s, now(), 2000, 2022, 100,
                          ARRAY['a'], 'h', 's', '/tmp/x')
                """,
                (model_id, slot),
            )

    insert("m1", "production")
    insert("m2", "challenger")
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert("m3", "production")


@pytest.mark.critical
def test_paper_bet_cannot_be_updated_or_deleted(db):
    """El ledger solo admite INSERT (brief §15)."""
    _seed_catalogue(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=True))
        cursor.execute(
            """
            INSERT INTO model_version (
                model_id, model_type, slot, trained_at, train_first_year,
                train_last_year, n_train_rows, feature_names, feature_hash,
                artifact_sha256, artifact_path
            ) VALUES ('m1', 'logreg', 'challenger', now(), 2000, 2022, 10,
                      ARRAY['a'], 'h', 's', '/tmp/x')
            """
        )
        cursor.execute(
            """
            INSERT INTO prediction (
                model_id, observation_id, observed_at, market_id, selection_id,
                probability, feature_hash
            ) SELECT 'm1', observation_id, observed_at, '1.001', 101, 0.6, 'h'
            FROM market_observation LIMIT 1
            RETURNING prediction_id
            """
        )
        prediction_id = cursor.fetchone()["prediction_id"]
        cursor.execute(
            """
            INSERT INTO paper_bet (
                prediction_id, model_id, market_id, selection_id, side,
                model_probability, market_probability_raw, market_probability_devig,
                entry_price, available_size, edge, expected_value,
                expected_value_after_costs, stake, liability, bankroll_before,
                market_book_snapshot, prev_hash, row_hash
            ) VALUES (%s, 'm1', '1.001', 101, 'BACK', 0.6, 0.55, 0.54, 1.8, 100,
                      0.06, 0.08, 0.05, 10, 0, 1000, '{}'::jsonb, 'p', 'r')
            """,
            (prediction_id,),
        )

    with db.transaction(), db.cursor() as cursor:
        cursor.execute("UPDATE paper_bet SET stake = 9999")
        cursor.execute("DELETE FROM paper_bet")

    with db.cursor() as cursor:
        cursor.execute("SELECT stake, count(*) OVER () AS n FROM paper_bet")
        row = cursor.fetchone()
        assert row is not None, "la apuesta no puede haberse borrado"
        assert float(row["stake"]) == 10.0, "la apuesta no puede haberse modificado"


@pytest.mark.critical
def test_purge_without_verified_archive_is_refused(db):
    """Ninguna observacion se borra sin exportacion verificada."""
    import psycopg

    with (
        pytest.raises(psycopg.errors.RaiseException, match="No existe exportacion"),
        db.transaction(),
        db.cursor() as cursor,
    ):
        cursor.execute("SELECT assert_archived('market_observation', 'nope')")


def test_archive_cannot_be_verified_with_mismatched_counts(db):
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation), db.transaction(), db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO archive_run (
                table_name, partition_name, period_start, period_end, parquet_path,
                row_count_db, row_count_file, file_sha256, file_bytes,
                verified, verified_at
            ) VALUES ('market_observation', 'p', '2026-01-01', '2026-02-01', '/tmp/p',
                      100, 99, 'h', 10, true, now())
            """
        )


# --- Vistas de analisis -------------------------------------------------------


def test_liquidity_emergence_view_works(db):
    _seed_catalogue(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=True))

    with db.cursor() as cursor:
        rows = repositories.liquidity_emergence(cursor)
    assert rows
    assert float(rows[0]["pct_con_liquidez"]) == 1.0


def test_market_states_feed_the_cadence(db):
    _seed_catalogue(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.save_observation(cursor, _payload(with_prices=True))

    with db.cursor() as cursor:
        states = repositories.load_market_states(cursor, ["1.001"])

    assert states["1.001"].has_shown_liquidity is True
    assert states["1.001"].is_active is True
    assert "6h" in states["1.001"].captured_labels

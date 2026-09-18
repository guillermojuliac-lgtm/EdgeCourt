"""Tests de la migracion Parquet -> PostgreSQL contra base real.

Cubre el camino que se uso para importar los snapshots historicos: sin perdida,
sin duplicados y sin tocar el fichero de origen.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.factories_betfair import catalogue, market_book

from edgecourt.db.import_parquet import import_snapshots, verify_import
from edgecourt.market import snapshots

pytestmark = pytest.mark.integration

pytest_plugins = ["tests.conftest_db"]

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _write_parquet(odds_dir, *, market_id="1.001", label="1h", empty: bool = False):
    """Escribe un Parquet en el formato plano antiguo."""
    book = market_book(market_id)
    if empty:
        for runner in book["runners"]:
            runner["ex"] = {"availableToBack": [], "availableToLay": []}

    rows = snapshots.normalise_market_book(
        book, catalogue(market_id, now=NOW), label=label, observed_at=NOW
    )
    frame = snapshots.to_frame(rows)
    snapshots.append_snapshots(odds_dir, frame)
    return frame


@pytest.fixture
def settings(settings_factory):
    s = settings_factory()
    s.ensure_directories()
    return s


@pytest.mark.critical
def test_import_preserves_every_capture(settings, db):
    _write_parquet(settings.odds_dir, market_id="1.001", label="1h")
    _write_parquet(settings.odds_dir, market_id="1.002", label="6h")

    report = import_snapshots(db, settings.odds_dir)

    assert report.parquet_rows == 4
    assert report.observations == 2
    assert report.markets == 2
    assert report.runners == 4
    assert report.skipped == []

    check = verify_import(db, settings.odds_dir)
    assert check["ok"]
    assert check["observations_in_db"] == 2


@pytest.mark.critical
def test_import_keeps_observations_without_prices(settings, db):
    """Las capturas vacias deben conservarse: son el dato de la liquidez."""
    _write_parquet(settings.odds_dir, market_id="1.001", label="12h", empty=True)

    report = import_snapshots(db, settings.odds_dir)

    assert report.observations == 1
    assert report.observations_without_prices == 1
    assert report.runner_prices == 0

    with db.cursor() as cursor:
        cursor.execute("SELECT has_prices, has_liquidity FROM market_observation")
        row = cursor.fetchone()
        assert row["has_prices"] is False
        assert row["has_liquidity"] is False
        cursor.execute("SELECT count(*) AS n FROM runner_price")
        assert cursor.fetchone()["n"] == 0
    db.commit()


@pytest.mark.critical
def test_import_is_idempotent(settings, db):
    _write_parquet(settings.odds_dir, market_id="1.001", label="1h")

    first = import_snapshots(db, settings.odds_dir)
    second = import_snapshots(db, settings.odds_dir)

    assert first.observations == second.observations == 1
    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) AS n FROM market_observation")
        assert cursor.fetchone()["n"] == 1
        cursor.execute("SELECT count(*) AS n FROM runner_price")
        assert cursor.fetchone()["n"] == 2
    db.commit()


@pytest.mark.critical
def test_import_does_not_modify_the_source_file(settings, db):
    import hashlib

    _write_parquet(settings.odds_dir, market_id="1.001", label="1h")
    files = sorted((settings.odds_dir / "betfair_match_odds").rglob("*.parquet"))
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]

    import_snapshots(db, settings.odds_dir)

    after = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    assert before == after, "la migracion no puede modificar el Parquet de origen"


def test_import_coexists_with_collector_writes(settings, db):
    """Importar despues de que el collector haya escrito no duplica."""
    from edgecourt.market.collector import Collector

    class _Client:
        def list_market_catalogue(self, market_filter, **kwargs):
            return [catalogue("1.001", now=NOW, start_in_minutes=60)]

        def list_market_book(self, market_ids):
            return [market_book(m) for m in market_ids]

    class _Sessions:
        def close(self):
            pass

    Collector(
        settings,
        client=_Client(),
        sessions=_Sessions(),
        connection=db,
        clock=lambda: NOW,
    ).run_cycle()

    _write_parquet(settings.odds_dir, market_id="1.001", label="1h")
    import_snapshots(db, settings.odds_dir)

    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) AS n FROM market_observation")
        assert cursor.fetchone()["n"] == 1, "no puede duplicar lo que ya escribio el collector"
    db.commit()


def test_import_without_parquet_is_harmless(settings, db):
    report = import_snapshots(db, settings.odds_dir)
    assert report.parquet_rows == 0
    assert report.observations == 0

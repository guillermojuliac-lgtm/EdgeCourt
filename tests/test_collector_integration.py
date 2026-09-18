"""Tests del collector contra PostgreSQL real.

Lo que se comprueba aqui no se puede verificar con una conexion simulada: que la
escritura es de verdad idempotente, que las transacciones aislan bien y que la
politica de cadencia lee el estado que el propio collector ha escrito.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.factories_betfair import catalogue, market_book

from edgecourt.market.collector import Collector

pytestmark = pytest.mark.integration

pytest_plugins = ["tests.conftest_db"]

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class _Client:
    def __init__(self, catalogues, *, empty_books: bool = False) -> None:
        self.catalogues = catalogues
        self.empty_books = empty_books
        self.book_calls: list[list[str]] = []

    def list_market_catalogue(self, market_filter, **kwargs):
        return self.catalogues

    def list_market_book(self, market_ids):
        self.book_calls.append(list(market_ids))
        books = []
        for market_id in market_ids:
            book = market_book(market_id)
            if self.empty_books:
                for runner in book["runners"]:
                    runner["ex"] = {"availableToBack": [], "availableToLay": []}
            books.append(book)
        return books


class _Sessions:
    def close(self) -> None:
        pass


@pytest.fixture
def settings(settings_factory):
    s = settings_factory(collector_interval_seconds=10.0)
    s.ensure_directories()
    return s


def _collector(settings, db, client, *, now=NOW, adaptive=True):
    return Collector(
        settings,
        client=client,
        sessions=_Sessions(),
        connection=db,
        clock=lambda: now,
        adaptive_enabled=adaptive,
    )


def _counts(db) -> dict[str, int]:
    out = {}
    with db.cursor() as cursor:
        for table in (
            "betfair_event",
            "betfair_market",
            "betfair_runner",
            "market_observation",
            "runner_price",
        ):
            cursor.execute(f"SELECT count(*) AS n FROM {table}")
            out[table] = cursor.fetchone()["n"]
    db.commit()
    return out


# --- Escritura ----------------------------------------------------------------


@pytest.mark.critical
def test_cycle_persists_catalogue_and_observation(settings, db):
    client = _Client([catalogue("1.001", now=NOW, start_in_minutes=60)])
    result = _collector(settings, db, client).run_cycle()

    assert result.observations_written == 1
    assert result.with_prices == 1

    counts = _counts(db)
    assert counts["betfair_event"] == 1
    assert counts["betfair_market"] == 1
    assert counts["betfair_runner"] == 2
    assert counts["market_observation"] == 1
    assert counts["runner_price"] == 2


@pytest.mark.critical
def test_observation_without_prices_persists_with_flags_false(settings, db):
    """El caso real: mercado OPEN, runners ACTIVE y sin libro."""
    client = _Client([catalogue("1.001", now=NOW, start_in_minutes=60)], empty_books=True)
    result = _collector(settings, db, client).run_cycle()

    assert result.without_prices == 1

    with db.cursor() as cursor:
        cursor.execute("SELECT has_prices, has_liquidity, total_available FROM market_observation")
        row = cursor.fetchone()
        assert row["has_prices"] is False
        assert row["has_liquidity"] is False
        assert float(row["total_available"]) == 0.0

        cursor.execute("SELECT count(*) AS n FROM runner_price")
        assert cursor.fetchone()["n"] == 0
    db.commit()


@pytest.mark.critical
def test_repeated_cycles_are_idempotent(settings, db):
    """Ejecutar el mismo ciclo dos veces no puede duplicar nada."""
    client = _Client([catalogue("1.001", now=NOW, start_in_minutes=60)])
    collector = _collector(settings, db, client)

    collector.run_cycle()
    first = _counts(db)
    collector.run_cycle()
    second = _counts(db)

    assert first == second
    assert second["market_observation"] == 1


@pytest.mark.critical
def test_second_cycle_skips_an_already_captured_milestone(settings, db):
    """La deduplicacion usa el estado leido de PostgreSQL, no memoria local."""
    client = _Client([catalogue("1.001", now=NOW, start_in_minutes=60)])

    first = _collector(settings, db, client).run_cycle()
    # Un collector NUEVO: no comparte estado en memoria con el anterior.
    second = _collector(settings, db, client).run_cycle()

    assert first.observations_written == 1
    assert second.snapshots_due == 0
    assert second.observations_written == 0


def test_catalogue_is_refreshed_without_duplicating(settings, db):
    client = _Client([catalogue("1.001", now=NOW, start_in_minutes=60)])
    _collector(settings, db, client).run_cycle()
    _collector(settings, db, client, now=NOW + timedelta(minutes=1)).run_cycle()

    counts = _counts(db)
    assert counts["betfair_event"] == 1
    assert counts["betfair_market"] == 1
    assert counts["betfair_runner"] == 2


# --- Cadencia adaptativa ------------------------------------------------------


@pytest.mark.critical
def test_adaptive_cadence_activates_after_liquidity_appears(settings, db):
    """Solo se sigue de cerca un mercado que ya ha mostrado liquidez."""
    cat = catalogue("1.001", now=NOW, start_in_minutes=60)
    client = _Client([cat])

    # El hito de 1h deja constancia de que hay liquidez.
    _collector(settings, db, client).run_cycle()

    # Veinte minutos despues no toca ningun hito, pero si cadencia adaptativa.
    later = NOW + timedelta(minutes=20)
    cat_later = catalogue("1.001", now=later, start_in_minutes=40)
    result = _collector(settings, db, _Client([cat_later]), now=later).run_cycle()

    assert result.observations_written == 1
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT snapshot_label, capture_key FROM market_observation ORDER BY observed_at"
        )
        labels = [(r["snapshot_label"], r["capture_key"]) for r in cursor.fetchall()]
    db.commit()

    assert labels[0] == ("1h", "1h")
    assert labels[1][0] == "adaptive"
    assert labels[1][1].startswith("a:")


def test_adaptive_does_not_activate_without_liquidity(settings, db):
    cat = catalogue("1.001", now=NOW, start_in_minutes=60)
    _collector(settings, db, _Client([cat], empty_books=True)).run_cycle()

    later = NOW + timedelta(minutes=20)
    cat_later = catalogue("1.001", now=later, start_in_minutes=40)
    result = _collector(settings, db, _Client([cat_later], empty_books=True), now=later).run_cycle()

    assert result.snapshots_due == 0


# --- Exportacion a Parquet ----------------------------------------------------


@pytest.mark.critical
def test_export_to_parquet_round_trips(settings, db):
    """Parquet es ahora salida, no entrada. Debe reflejar lo que hay en la BD."""
    from datetime import date

    from edgecourt.db.export_parquet import export_range, verify_export

    client = _Client(
        [
            catalogue("1.001", now=NOW, start_in_minutes=60),
            catalogue("1.002", now=NOW, start_in_minutes=10),
        ]
    )
    _collector(settings, db, client).run_cycle()

    start, end = date(2026, 9, 1), date(2026, 10, 1)
    result = export_range(db, settings.odds_dir, start=start, end=end)

    assert result.observations == 2
    assert result.with_prices == 2
    assert result.rows == 4  # dos runners por observacion

    check = verify_export(db, settings.odds_dir, start=start, end=end)
    assert check["ok"]
    assert check["observations_in_db"] == check["observations_in_file"] == 2


def test_export_preserves_observations_without_prices(settings, db):
    """Las observaciones vacias tambien se exportan: son el dato de la liquidez."""
    from datetime import date

    from edgecourt.db.export_parquet import export_range

    client = _Client([catalogue("1.001", now=NOW, start_in_minutes=60)], empty_books=True)
    _collector(settings, db, client).run_cycle()

    result = export_range(db, settings.odds_dir, start=date(2026, 9, 1), end=date(2026, 10, 1))
    assert result.observations == 1
    assert result.without_prices == 1

    from edgecourt.storage import read_parquet

    frame = read_parquet(settings.odds_dir / "betfair_match_odds")
    assert len(frame) == 1
    assert bool(frame["has_prices"].iloc[0]) is False


def test_export_of_empty_range_is_harmless(settings, db):
    from datetime import date

    from edgecourt.db.export_parquet import export_range

    result = export_range(db, settings.odds_dir, start=date(2020, 1, 1), end=date(2020, 2, 1))
    assert result.rows == 0
    assert result.destination is None

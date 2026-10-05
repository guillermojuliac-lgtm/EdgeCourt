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


# --- Incidente del 2026-09-29: desbordamiento de max_spread_pct ---------------


def _column_type(db, table: str, column: str) -> tuple[int, int]:
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        )
        row = cursor.fetchone()
    db.commit()
    return row["numeric_precision"], row["numeric_scale"]


def _stored_spreads(db) -> dict[str, object]:
    with db.cursor() as cursor:
        cursor.execute("SELECT market_id, max_spread_pct FROM market_observation")
        rows = {r["market_id"]: r["max_spread_pct"] for r in cursor.fetchall()}
    db.commit()
    return rows


class _BookClient(_Client):
    """Cliente con un libro concreto por mercado."""

    def __init__(self, catalogues, books) -> None:
        super().__init__(catalogues)
        self.books = books

    def list_market_book(self, market_ids):
        return [self.books[m] for m in market_ids]


def test_max_spread_pct_column_is_numeric_12_4(db):
    assert _column_type(db, "market_observation", "max_spread_pct") == (12, 4)


@pytest.mark.critical
@pytest.mark.parametrize(
    ("back", "lay", "expected"),
    [
        # Valores del incidente real (Marcinko v Frech, 1.263050661).
        (1.09, 190.0, "17331.1927"),
        (1.40, 190.0, "13471.4286"),
        # Cota maxima de la escala de Betfair: 1,01 / 1.000.
        (1.01, 1000.0, "98909.9010"),
    ],
)
def test_extreme_spread_is_stored_without_clamp(settings, db, back, lay, expected):
    """El spread real se conserva: ni desbordamiento, ni truncado, ni clamp."""
    from decimal import Decimal

    market = catalogue("1.263050661", now=NOW, start_in_minutes=60)
    book = market_book("1.263050661", back_a=back, lay_a=lay)
    result = _collector(settings, db, _BookClient([market], {"1.263050661": book})).run_cycle()

    assert result.failed_markets == []
    assert result.observations_written == 1
    assert _stored_spreads(db)["1.263050661"] == Decimal(expected)


@pytest.mark.critical
def test_invalid_market_is_rolled_back_alone(settings, db):
    """A valido, B rechazado por un CHECK real de PostgreSQL, C valido."""
    markets = [catalogue(m, now=NOW, start_in_minutes=60) for m in ("1.001", "1.002", "1.003")]
    books = {
        "1.001": market_book("1.001"),
        # Cuota por debajo de 1,01: la rechaza runner_price_back_valid.
        "1.002": market_book("1.002", back_a=1.0),
        "1.003": market_book("1.003"),
    }
    collector = _collector(settings, db, _BookClient(markets, books))

    result = collector.run_cycle()

    assert result.failed_markets == ["1.002"]
    assert result.observations_written == 2
    stored = _stored_spreads(db)
    assert set(stored) == {"1.001", "1.003"}
    # Ni la observacion ni los precios de B: su transaccion se deshizo entera.
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS n FROM runner_price p JOIN market_observation o "
            "USING (observation_id, observed_at) WHERE o.market_id = '1.002'"
        )
        assert cursor.fetchone()["n"] == 0
    db.commit()

    # Y el collector sigue funcionando: un ciclo posterior es idempotente.
    again = collector.run_cycle()
    assert again.failed_markets in ([], ["1.002"])
    assert set(_stored_spreads(db)) == {"1.001", "1.003"}


@pytest.mark.critical
def test_migration_004_upgrades_an_existing_database_preserving_data(settings, db, tmp_path):
    """Ruta real de produccion: esquema 001-003 con datos -> se aplica 004 -> nada cambia."""
    import shutil
    from decimal import Decimal

    from edgecourt.db.migrate import MIGRATIONS_DIR, current_version, migrate

    # Rehacer el esquema solo hasta la 003, como estaba produccion.
    with db.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")
    db.commit()
    old = tmp_path / "old"
    old.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("00[1-3]_*.sql")):
        shutil.copy(path, old / path.name)
    migrate(db, old)
    assert current_version(db) == 3
    assert _column_type(db, "market_observation", "max_spread_pct") == (8, 4)

    market = catalogue("1.001", now=NOW, start_in_minutes=60)
    _collector(settings, db, _BookClient([market], {"1.001": market_book("1.001")})).run_cycle()
    before = _stored_spreads(db)

    applied = migrate(db)
    # La 004 es la primera pendiente; las posteriores (p. ej. la 005, particionado UTC)
    # se aplican a continuacion y no deben alterar los datos.
    assert [m.version for m in applied][0] == 4
    assert _column_type(db, "market_observation", "max_spread_pct") == (12, 4)
    assert _stored_spreads(db) == before  # datos existentes intactos
    assert isinstance(before["1.001"], Decimal)

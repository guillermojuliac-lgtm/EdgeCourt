"""Tests del bucle del collector con PostgreSQL como destino.

Los que no necesitan base de datos usan una conexion simulada; los que
comprueban idempotencia y transaccionalidad reales estan en
`test_collector_integration.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.factories_betfair import catalogue, market_book

from edgecourt.market.collector import Collector, CycleResult

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class _FakeClient:
    """Cliente de lectura simulado. Registra lo que se le pide."""

    def __init__(self, catalogues=None, *, fail_with: Exception | None = None) -> None:
        self.catalogues = catalogues or []
        self.fail_with = fail_with
        self.catalogue_calls = 0
        self.book_calls: list[list[str]] = []

    def list_market_catalogue(self, market_filter, **kwargs):
        self.catalogue_calls += 1
        if self.fail_with:
            raise self.fail_with
        return self.catalogues

    def list_market_book(self, market_ids):
        self.book_calls.append(list(market_ids))
        return [market_book(m) for m in market_ids]

    def list_events(self, market_filter):  # pragma: no cover
        return []


class _FakeSessions:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeCursor:
    def __init__(self, owner):
        self._owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        self._owner.statements.append(args[0] if args else "")

    def fetchone(self):
        # Cubre todas las consultas que hace el collector: el advisory lock,
        # el id de observacion devuelto por el INSERT y la creacion de particiones.
        return {"acquired": True, "observation_id": 1, "name": "x", "pid": 0}

    def fetchall(self):
        return []


class _FakeTransaction:
    def __init__(self, owner):
        self._owner = owner

    def __enter__(self):
        self._owner.transactions += 1
        return self

    def __exit__(self, *args):
        return False


class _FakeConnection:
    """Conexion simulada: cuenta transacciones y sentencias.

    Responde al advisory lock concediendolo siempre; la exclusion real entre
    procesos se comprueba en los tests de integracion.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self)

    def transaction(self):
        return _FakeTransaction(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def collector_settings(settings_factory):
    settings = settings_factory(collector_interval_seconds=10.0)
    settings.ensure_directories()
    return settings


def _collector(settings, client, *, now=NOW, connection=None, adaptive=True):
    return Collector(
        settings,
        client=client,
        sessions=_FakeSessions(),
        connection=connection or _FakeConnection(),
        clock=lambda: now,
        adaptive_enabled=adaptive,
    )


# --- Ciclo --------------------------------------------------------------------


def test_cycle_without_markets_does_nothing(collector_settings):
    client = _FakeClient([])
    connection = _FakeConnection()
    result = _collector(collector_settings, client, connection=connection).run_cycle()

    assert result.markets_seen == 0
    assert result.observations_written == 0
    assert not client.book_calls
    assert connection.transactions == 0


def test_cycle_captures_a_due_milestone(collector_settings):
    client = _FakeClient([catalogue(now=NOW, start_in_minutes=60)])
    result = _collector(collector_settings, client).run_cycle()

    assert result.markets_seen == 1
    assert result.snapshots_due == 1
    assert result.observations_written == 1
    assert result.labels == {"1h": 1}


@pytest.mark.critical
def test_observation_without_prices_is_still_written(collector_settings):
    """Un mercado OPEN sin BACK/LAY se registra igualmente.

    Es el caso real que motivo el rediseno del almacenamiento: informacion
    valida, no un fallo de captura.
    """

    class _EmptyBookClient(_FakeClient):
        def list_market_book(self, market_ids):
            self.book_calls.append(list(market_ids))
            books = []
            for market_id in market_ids:
                book = market_book(market_id)
                for runner in book["runners"]:
                    runner["ex"] = {"availableToBack": [], "availableToLay": []}
                books.append(book)
            return books

    client = _EmptyBookClient([catalogue(now=NOW, start_in_minutes=60)])
    result = _collector(collector_settings, client).run_cycle()

    assert result.observations_written == 1
    assert result.without_prices == 1
    assert result.with_prices == 0


@pytest.mark.critical
def test_each_market_is_written_in_its_own_transaction(collector_settings):
    """Nunca puede quedar una observacion con has_prices pero sin precios."""
    connection = _FakeConnection()
    client = _FakeClient(
        [
            catalogue("1.001", now=NOW, start_in_minutes=60),
            catalogue("1.002", now=NOW, start_in_minutes=60),
        ]
    )
    _collector(collector_settings, client, connection=connection).run_cycle()

    # Una para el catalogo y una por cada mercado observado.
    assert connection.transactions >= 3


def test_markets_not_due_are_skipped(collector_settings):
    client = _FakeClient([catalogue(now=NOW, start_in_minutes=240)])
    result = _collector(collector_settings, client).run_cycle()

    assert result.markets_seen == 1
    assert result.snapshots_due == 0
    assert not client.book_calls


def test_labels_are_batched_separately(collector_settings):
    client = _FakeClient(
        [
            catalogue("1.001", now=NOW, start_in_minutes=60),
            catalogue("1.002", now=NOW, start_in_minutes=10),
        ]
    )
    result = _collector(collector_settings, client).run_cycle()

    assert result.labels == {"1h": 1, "10m": 1}
    assert len(client.book_calls) == 2


def test_catalogue_window_covers_the_furthest_milestone(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    window = collector._catalogue_window(NOW).as_payload()["marketStartTime"]
    horizon = datetime.fromisoformat(window["to"].replace("Z", "+00:00"))
    assert horizon - NOW >= timedelta(hours=24)


def test_books_without_catalogue_are_skipped(collector_settings):
    class _Mismatched(_FakeClient):
        def list_market_book(self, market_ids):
            self.book_calls.append(list(market_ids))
            return [market_book("1.desconocido")]

    client = _Mismatched([catalogue("1.001", now=NOW, start_in_minutes=60)])
    result = _collector(collector_settings, client).run_cycle()
    assert result.observations_written == 0


# --- Resiliencia --------------------------------------------------------------


@pytest.mark.critical
def test_a_failing_cycle_does_not_kill_the_process(collector_settings):
    """Brief §20: el collector debe recuperarse de errores temporales."""
    client = _FakeClient([], fail_with=RuntimeError("caida temporal"))
    collector = _collector(collector_settings, client)
    collector._stop.wait = lambda _s: None

    cycles = collector.run(max_cycles=3)

    assert cycles == 3
    assert client.catalogue_calls == 3


@pytest.mark.critical
def test_a_failed_cycle_rolls_back_the_connection(collector_settings):
    """Una transaccion a medias no puede envenenar el ciclo siguiente."""
    connection = _FakeConnection()
    client = _FakeClient([], fail_with=RuntimeError("fallo"))
    collector = _collector(collector_settings, client, connection=connection)
    collector._stop.wait = lambda _s: None

    collector.run(max_cycles=2)
    assert connection.rollbacks == 2


def test_wait_grows_with_consecutive_failures(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    base = float(collector_settings.collector_interval_seconds)

    assert collector._wait_seconds(base) == base
    collector._consecutive_failures = 1
    assert collector._wait_seconds(base) > base
    collector._consecutive_failures = 50
    assert collector._wait_seconds(base) <= 300.0


def test_failure_counter_resets_after_a_good_cycle(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    collector._consecutive_failures = 3
    collector._stop.wait = lambda _s: None

    collector.run(max_cycles=1)
    assert collector._consecutive_failures == 0


@pytest.mark.critical
def test_missing_credentials_abort_instead_of_retrying(collector_settings):
    """Un fallo de configuracion no es temporal y no debe reintentarse en bucle."""
    from edgecourt.market.auth import MissingCredentialsError

    client = _FakeClient([], fail_with=MissingCredentialsError("faltan BETFAIR_*"))
    sessions = _FakeSessions()
    collector = Collector(
        collector_settings,
        client=client,
        sessions=sessions,
        connection=_FakeConnection(),
        clock=lambda: NOW,
    )

    with pytest.raises(MissingCredentialsError):
        collector.run(max_cycles=10)

    assert client.catalogue_calls == 1
    assert sessions.closed


# --- Apagado ------------------------------------------------------------------


@pytest.mark.critical
def test_stop_request_ends_the_loop_and_closes_the_session(collector_settings):
    client = _FakeClient([])
    sessions = _FakeSessions()
    collector = Collector(
        collector_settings,
        client=client,
        sessions=sessions,
        connection=_FakeConnection(),
        clock=lambda: NOW,
    )

    collector.request_stop()
    cycles = collector.run()

    assert cycles == 0
    assert collector.stopping
    assert sessions.closed


@pytest.mark.critical
def test_session_is_closed_even_if_the_loop_crashes(collector_settings):
    sessions = _FakeSessions()
    collector = Collector(
        collector_settings,
        client=_FakeClient([]),
        sessions=sessions,
        connection=_FakeConnection(),
        clock=lambda: NOW,
    )

    def explode(_seconds):
        raise KeyboardInterrupt

    collector._stop.wait = explode
    with pytest.raises(KeyboardInterrupt):
        collector.run()

    assert sessions.closed


@pytest.mark.critical
def test_no_wait_after_the_last_cycle(collector_settings):
    waits: list[float] = []
    collector = _collector(collector_settings, _FakeClient([]))
    collector._stop.wait = lambda seconds: waits.append(seconds)

    collector.run(max_cycles=2)
    assert len(waits) == 1


def test_signal_handlers_can_be_installed(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    collector.install_signal_handlers()
    assert not collector.stopping


def test_cycle_result_reports_success():
    assert CycleResult().ok
    assert not CycleResult(error="algo").ok


def test_run_id_identifies_the_execution(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    assert collector.run_id is not None
    assert collector.run_id == collector.run_id


# --- El collector ya no escribe Parquet ---------------------------------------


@pytest.mark.critical
def test_collector_writes_nothing_to_the_odds_directory(collector_settings):
    """Parquet dejo de ser destino primario: ahora es solo exportacion."""
    client = _FakeClient([catalogue(now=NOW, start_in_minutes=60)])
    before = {p for p in collector_settings.odds_dir.rglob("*") if p.is_file()}

    _collector(collector_settings, client).run_cycle()

    after = {p for p in collector_settings.odds_dir.rglob("*") if p.is_file()}
    assert after == before, f"el collector escribio en data/odds: {sorted(after - before)}"

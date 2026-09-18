"""Tests del bucle del collector: ciclos, resiliencia y apagado limpio."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.factories_betfair import catalogue, market_book

from edgecourt.market import snapshots
from edgecourt.market.collector import Collector, CycleResult
from edgecourt.storage import read_parquet

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

    def list_events(self, market_filter):  # pragma: no cover - no usado aqui
        return []


class _FakeSessions:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def collector_settings(settings_factory):
    settings = settings_factory(collector_interval_seconds=10.0)
    settings.ensure_directories()
    return settings


def _collector(settings, client, *, now=NOW):
    return Collector(settings, client=client, sessions=_FakeSessions(), clock=lambda: now)


# --- Ciclo --------------------------------------------------------------------


def test_cycle_without_markets_writes_nothing(collector_settings):
    client = _FakeClient([])
    result = _collector(collector_settings, client).run_cycle()

    assert result.markets_seen == 0
    assert result.rows_written == 0
    assert not client.book_calls


def test_cycle_captures_a_due_market(collector_settings):
    client = _FakeClient([catalogue(now=NOW, start_in_minutes=60)])
    result = _collector(collector_settings, client).run_cycle()

    assert result.markets_seen == 1
    assert result.snapshots_due == 1
    assert result.rows_written == 2  # dos jugadores
    assert result.labels == {"1h": 1}

    stored = read_parquet(collector_settings.odds_dir / snapshots.SNAPSHOTS_DATASET)
    assert set(stored["snapshot_label"]) == {"1h"}
    assert set(stored["runner_name"]) == {"Carlos Alcaraz", "Jannik Sinner"}


def test_cycle_ignores_markets_that_are_not_due(collector_settings):
    client = _FakeClient([catalogue(now=NOW, start_in_minutes=240)])
    result = _collector(collector_settings, client).run_cycle()

    assert result.markets_seen == 1
    assert result.snapshots_due == 0
    assert not client.book_calls


@pytest.mark.critical
def test_second_cycle_does_not_recapture_the_same_target(collector_settings):
    """La deduplicacion debe sobrevivir entre ciclos, leyendo lo ya almacenado."""
    client = _FakeClient([catalogue(now=NOW, start_in_minutes=60)])
    collector = _collector(collector_settings, client)

    first = collector.run_cycle()
    second = collector.run_cycle()

    assert first.rows_written == 2
    assert second.snapshots_due == 0
    assert second.rows_written == 0
    assert len(client.book_calls) == 1


def test_different_targets_are_captured_in_separate_batches(collector_settings):
    client = _FakeClient(
        [
            catalogue("1.001", now=NOW, start_in_minutes=60),
            catalogue("1.002", now=NOW, start_in_minutes=10),
        ]
    )
    result = _collector(collector_settings, client).run_cycle()

    assert result.labels == {"1h": 1, "10m": 1}
    # Un lote por hito: una respuesta no puede repartirse entre dos etiquetas.
    assert len(client.book_calls) == 2
    assert client.book_calls[0] == ["1.001"]
    assert client.book_calls[1] == ["1.002"]


def test_catalogue_window_covers_the_furthest_target(collector_settings):
    """La ventana de consulta debe alcanzar mas alla del hito de 24h."""
    client = _FakeClient([])
    collector = _collector(collector_settings, client)
    market_filter = collector._catalogue_window(NOW)

    window = market_filter.as_payload()["marketStartTime"]
    horizon = datetime.fromisoformat(window["to"].replace("Z", "+00:00"))
    assert horizon - NOW >= timedelta(hours=24)


def test_books_without_matching_catalogue_are_skipped(collector_settings):
    class _Mismatched(_FakeClient):
        def list_market_book(self, market_ids):
            self.book_calls.append(list(market_ids))
            return [market_book("1.desconocido")]

    client = _Mismatched([catalogue("1.001", now=NOW, start_in_minutes=60)])
    result = _collector(collector_settings, client).run_cycle()
    assert result.rows_written == 0


# --- Resiliencia --------------------------------------------------------------


@pytest.mark.critical
def test_a_failing_cycle_does_not_kill_the_process(collector_settings):
    """Brief §20: el collector debe recuperarse de errores temporales."""
    client = _FakeClient([], fail_with=RuntimeError("caida temporal de Betfair"))
    collector = _collector(collector_settings, client)
    collector._stop.wait = lambda _s: None  # no esperar en el test

    cycles = collector.run(max_cycles=3)

    assert cycles == 3, "el proceso debe seguir vivo tras los fallos"
    assert client.catalogue_calls == 3


def test_wait_grows_with_consecutive_failures(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    base = float(collector_settings.collector_interval_seconds)

    assert collector._wait_seconds(base) == base
    collector._consecutive_failures = 1
    assert collector._wait_seconds(base) > base
    collector._consecutive_failures = 3
    growing = collector._wait_seconds(base)
    collector._consecutive_failures = 10
    assert collector._wait_seconds(base) >= growing


def test_wait_is_capped(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    collector._consecutive_failures = 50
    assert collector._wait_seconds(60.0) <= 300.0


def test_failure_counter_resets_after_a_good_cycle(collector_settings):
    client = _FakeClient([])
    collector = _collector(collector_settings, client)
    collector._consecutive_failures = 3
    collector._stop.wait = lambda _s: None

    collector.run(max_cycles=1)
    assert collector._consecutive_failures == 0


# --- Apagado ------------------------------------------------------------------


@pytest.mark.critical
def test_stop_request_ends_the_loop_and_closes_the_session(collector_settings):
    """Apagado limpio: SIGTERM debe terminar el bucle y cerrar sesion en Betfair."""
    client = _FakeClient([])
    sessions = _FakeSessions()
    collector = Collector(collector_settings, client=client, sessions=sessions, clock=lambda: NOW)

    collector.request_stop()
    cycles = collector.run()

    assert cycles == 0
    assert collector.stopping
    assert sessions.closed, "la sesion debe cerrarse al apagar"


@pytest.mark.critical
def test_session_is_closed_even_if_the_loop_crashes(collector_settings):
    """Un fallo inesperado no puede dejar la sesion abierta en Betfair."""
    sessions = _FakeSessions()
    collector = Collector(
        collector_settings, client=_FakeClient([]), sessions=sessions, clock=lambda: NOW
    )

    def explode(_seconds):
        raise KeyboardInterrupt

    collector._stop.wait = explode
    with pytest.raises(KeyboardInterrupt):
        collector.run()

    assert sessions.closed


def test_signal_handlers_can_be_installed(collector_settings):
    collector = _collector(collector_settings, _FakeClient([]))
    collector.install_signal_handlers()  # no debe lanzar
    assert not collector.stopping


def test_cycle_result_reports_success():
    assert CycleResult().ok
    assert not CycleResult(error="algo").ok


@pytest.mark.critical
def test_missing_credentials_abort_instead_of_retrying(collector_settings):
    """Un fallo de configuracion no es temporal y no debe reintentarse en bucle.

    Reintentar cada minuto durante horas esconderia el problema en los logs en
    lugar de hacerlo visible. Se propaga para que el operador lo vea enseguida.
    """
    from edgecourt.market.auth import MissingCredentialsError

    client = _FakeClient([], fail_with=MissingCredentialsError("faltan BETFAIR_*"))
    sessions = _FakeSessions()
    collector = Collector(collector_settings, client=client, sessions=sessions, clock=lambda: NOW)

    with pytest.raises(MissingCredentialsError):
        collector.run(max_cycles=10)

    assert client.catalogue_calls == 1, "no debe reintentar un error de configuracion"
    assert sessions.closed, "la sesion debe cerrarse igualmente"


@pytest.mark.critical
def test_no_wait_after_the_last_cycle(collector_settings):
    """Con `max_cycles`, el proceso termina sin esperar el intervalo.

    Regresion: una ejecucion puntual de un solo ciclo se quedaba colgada
    esperando el intervalo completo antes de salir.
    """
    waits: list[float] = []
    collector = _collector(collector_settings, _FakeClient([]))
    collector._stop.wait = lambda seconds: waits.append(seconds)

    collector.run(max_cycles=2)
    assert len(waits) == 1, "solo debe esperarse entre ciclos, nunca tras el ultimo"

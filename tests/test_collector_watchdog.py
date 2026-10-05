"""Watchdog de systemd del collector (sd_notify) y salud de particiones.

Incidente del 2026-10-01: systemd veia `active` un proceso cuyos ciclos fallaban
indefinidamente. `WATCHDOG=1` solo se envia tras un ciclo completado sin excepcion.
"""

from __future__ import annotations

import re
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.test_betfair_collector import NOW, _FakeClient, _FakeConnection, _FakeSessions

from edgecourt.db import health
from edgecourt.market import collector as collector_module
from edgecourt.market.collector import Collector
from edgecourt.systemd_notify import SystemdNotifier

pytest_plugins = ["tests.conftest_db"]

UNIT = Path(__file__).resolve().parent.parent / "deploy" / "edgecourt-collector.service"


class _Recorder(SystemdNotifier):
    """Notificador que apunta los mensajes en lugar de enviarlos."""

    def __init__(self) -> None:
        super().__init__("/fake")
        self.sent: list[str] = []

    def notify(self, message: str) -> bool:
        self.sent.append(message)
        return True


class _FlakyClient(_FakeClient):
    """Falla en las llamadas cuyo indice (base 0) esta en `fail_on`."""

    def __init__(self, fail_on: set[int], catalogues=None) -> None:
        super().__init__(catalogues)
        self.fail_on = fail_on

    def list_market_catalogue(self, market_filter, **kwargs):
        call = self.catalogue_calls
        self.catalogue_calls += 1
        if call in self.fail_on:
            raise RuntimeError("fallo simulado")
        return self.catalogues


def _collector(settings_factory, client, notifier):
    c = Collector(
        settings_factory(),
        client=client,
        sessions=_FakeSessions(),
        connection=_FakeConnection(),
        clock=lambda: NOW,
        notifier=notifier,
    )
    c._stop.wait = lambda _timeout=None: False  # sin esperas reales
    return c


# --- Notificador ----------------------------------------------------------------


def test_no_notify_socket_is_a_noop():
    notifier = SystemdNotifier.from_env({})
    assert not notifier.enabled
    assert notifier.ready() is False
    assert notifier.watchdog() is False
    assert notifier.stopping() is False


def test_dead_socket_never_raises(tmp_path):
    notifier = SystemdNotifier(str(tmp_path / "no-existe.sock"))
    assert notifier.watchdog() is False
    assert notifier.watchdog() is False


def test_message_reaches_the_socket(tmp_path):
    path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(2)
    try:
        notifier = SystemdNotifier.from_env({"NOTIFY_SOCKET": path})
        assert notifier.enabled
        assert notifier.ready() and notifier.watchdog() and notifier.stopping()
        received = [server.recv(256).decode() for _ in range(3)]
    finally:
        server.close()
    assert received == ["READY=1", "WATCHDOG=1", "STOPPING=1"]


def test_abstract_socket_address(monkeypatch):
    connected = []

    class _Sock:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect(self, address):
            connected.append(address)

        def sendall(self, data):
            pass

    monkeypatch.setattr("edgecourt.systemd_notify.socket.socket", _Sock)
    assert SystemdNotifier("@abstracto").watchdog()
    assert connected == ["\0abstracto"]


def test_messages_are_fixed_constants_without_secrets():
    from edgecourt import systemd_notify as module

    for message in (module.READY, module.WATCHDOG, module.STOPPING):
        assert re.fullmatch(r"[A-Z]+=1", message)


# --- Ciclo y watchdog -----------------------------------------------------------


@pytest.mark.critical
def test_successful_cycle_sends_watchdog(settings_factory):
    rec = _Recorder()
    _collector(settings_factory, _FakeClient([]), rec).run(max_cycles=1)
    assert rec.sent.count("WATCHDOG=1") == 1


@pytest.mark.critical
def test_empty_cycle_with_zero_markets_is_healthy(settings_factory):
    rec = _Recorder()
    client = _FakeClient([])
    _collector(settings_factory, client, rec).run(max_cycles=3)
    assert client.catalogue_calls == 3
    assert rec.sent.count("WATCHDOG=1") == 3


@pytest.mark.critical
def test_failed_cycle_sends_no_watchdog(settings_factory):
    rec = _Recorder()
    client = _FakeClient(fail_with=RuntimeError("boom"))
    _collector(settings_factory, client, rec).run(max_cycles=1)
    assert "WATCHDOG=1" not in rec.sent


@pytest.mark.critical
def test_consecutive_failures_send_no_false_signal(settings_factory):
    rec = _Recorder()
    c = _collector(settings_factory, _FakeClient(fail_with=RuntimeError("boom")), rec)
    c.run(max_cycles=6)
    assert c._consecutive_failures == 6  # el backoff sigue funcionando
    assert "WATCHDOG=1" not in rec.sent


@pytest.mark.critical
def test_recovery_resumes_watchdog(settings_factory):
    rec = _Recorder()
    c = _collector(settings_factory, _FlakyClient({0, 1, 2}), rec)
    c.run(max_cycles=5)
    assert rec.sent.count("WATCHDOG=1") == 2  # 3 fallos sin senal; 2 ciclos sanos
    assert c._consecutive_failures == 0


def test_missing_credentials_sends_no_watchdog(settings_factory):
    from edgecourt.market.auth import MissingCredentialsError

    rec = _Recorder()
    client = _FakeClient(fail_with=MissingCredentialsError("x"))
    with pytest.raises(MissingCredentialsError):
        _collector(settings_factory, client, rec).run(max_cycles=1)
    assert "WATCHDOG=1" not in rec.sent


# --- READY / STOPPING -----------------------------------------------------------


def test_ready_precedes_first_watchdog_and_stopping_is_last(settings_factory):
    rec = _Recorder()
    _collector(settings_factory, _FakeClient([]), rec).run(max_cycles=2)
    assert rec.sent == ["READY=1", "WATCHDOG=1", "WATCHDOG=1", "STOPPING=1"]


def test_ready_is_sent_before_any_cycle_even_if_cycles_fail(settings_factory):
    rec = _Recorder()
    _collector(settings_factory, _FakeClient(fail_with=RuntimeError("x")), rec).run(max_cycles=1)
    assert rec.sent[0] == "READY=1"


def test_clean_stop_signals_stopping_and_closes_sessions(settings_factory):
    rec = _Recorder()
    sessions = _FakeSessions()
    c = Collector(
        settings_factory(),
        client=_FakeClient([]),
        sessions=sessions,
        connection=_FakeConnection(),
        clock=lambda: NOW,
        notifier=rec,
    )
    c.request_stop()
    assert c.run() == 0
    assert sessions.closed
    assert rec.sent[-1] == "STOPPING=1"
    assert "WATCHDOG=1" not in rec.sent


def test_collector_runs_outside_systemd(settings_factory, monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    c = Collector(
        settings_factory(),
        client=_FakeClient([]),
        sessions=_FakeSessions(),
        connection=_FakeConnection(),
        clock=lambda: NOW,
    )
    assert not c._notifier.enabled
    c._stop.wait = lambda _t=None: False
    assert c.run(max_cycles=2) == 2


# --- WatchdogSec frente al ritmo del bucle ----------------------------------------

# Supuestos del peor caso (ver docs/audits/2026-10-collector-watchdog.md).
FAILED_CYCLE_SECONDS = 30.0  # una llamada de red agota REQUEST_TIMEOUT_SECONDS
HEALTHY_CYCLE_SECONDS = 270.0  # catalogo + hasta ~8 lotes de listMarketBook


def _unit_watchdog_seconds() -> float:
    match = re.search(r"^WatchdogSec=(\d+)\s*$", UNIT.read_text(), re.MULTILINE)
    assert match, "la unidad debe definir WatchdogSec"
    return float(match.group(1))


def _gap_with_failures(settings_factory, failures: int) -> float:
    """Hueco maximo entre dos WATCHDOG con `failures` ciclos fallidos en medio."""
    c = Collector(settings_factory(), client=_FakeClient([]), sessions=_FakeSessions())
    interval = float(c._settings.collector_interval_seconds)
    gap = interval
    for n in range(1, failures + 1):
        c._consecutive_failures = n
        gap += FAILED_CYCLE_SECONDS + c._wait_seconds(interval)
    return gap + HEALTHY_CYCLE_SECONDS


def test_unit_declares_notify_and_watchdog():
    text = UNIT.read_text()
    assert re.search(r"^Type=notify\s*$", text, re.MULTILINE)
    assert re.search(r"^Restart=on-failure\s*$", text, re.MULTILINE)
    assert re.search(r"^NotifyAccess=main\s*$", text, re.MULTILINE)


def test_watchdog_exceeds_worst_healthy_gap(settings_factory):
    watchdog = _unit_watchdog_seconds()
    assert watchdog > _gap_with_failures(settings_factory, 0)
    # fallos transitorios razonables no disparan el watchdog
    assert watchdog > _gap_with_failures(settings_factory, 2)
    assert watchdog > _gap_with_failures(settings_factory, 3)


def test_watchdog_expires_on_persistent_failure(settings_factory):
    assert _unit_watchdog_seconds() == 1200
    # un fallo persistente (4 o mas fallos seguidos) supera el watchdog
    assert _unit_watchdog_seconds() < _gap_with_failures(settings_factory, 4)
    assert _unit_watchdog_seconds() < _gap_with_failures(settings_factory, 5)


def test_long_wait_is_below_watchdog():
    assert _unit_watchdog_seconds() > collector_module.LONG_WAIT_SECONDS * 2


# --- Salud de particiones (solo lectura) -----------------------------------------


class _PartitionCursor:
    def __init__(self, bounds: dict[str, tuple[datetime, datetime]]):
        self.bounds = bounds
        self.statements: list[str] = []
        self._name = None

    def execute(self, sql, params=()):
        self.statements.append(sql)
        self._name = params[0] if params else None

    def fetchone(self):
        found = self.bounds.get(self._name)
        return None if found is None else {"lower_bound": found[0], "upper_bound": found[1]}


def _utc(year, month, day=1):
    return datetime(year, month, day, tzinfo=UTC)


def _all_bounds():
    return {
        "market_observation_202610": (_utc(2026, 10), _utc(2026, 11)),
        "market_observation_202611": (_utc(2026, 11), _utc(2026, 12)),
        "runner_price_202610": (_utc(2026, 10), _utc(2026, 11)),
        "runner_price_202611": (_utc(2026, 11), _utc(2026, 12)),
    }


NOW_OCT = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


def test_partition_health_ok():
    cursor = _PartitionCursor(_all_bounds())
    assert health.partition_warnings(cursor, NOW_OCT) == []
    assert all(s.lstrip().upper().startswith("SELECT") for s in cursor.statements)


def test_partition_health_reports_missing_next_month():
    bounds = _all_bounds()
    del bounds["runner_price_202611"]
    warnings = health.partition_warnings(_PartitionCursor(bounds), NOW_OCT)
    assert warnings == ["falta la particion runner_price_202611"]


def test_partition_health_reports_madrid_bounds():
    from zoneinfo import ZoneInfo

    madrid = ZoneInfo("Europe/Madrid")
    bounds = _all_bounds()
    bounds["market_observation_202610"] = (
        datetime(2026, 10, 1, tzinfo=madrid),
        datetime(2026, 11, 1, tzinfo=madrid),
    )
    warnings = health.partition_warnings(_PartitionCursor(bounds), NOW_OCT)
    assert len(warnings) == 1
    assert "market_observation_202610" in warnings[0]
    assert "2026-09-30T22:00:00Z" in warnings[0]


def test_partition_health_december_rolls_into_next_year():
    cursor = _PartitionCursor(
        {
            "market_observation_202612": (_utc(2026, 12), _utc(2027, 1)),
            "market_observation_202701": (_utc(2027, 1), _utc(2027, 2)),
            "runner_price_202612": (_utc(2026, 12), _utc(2027, 1)),
            "runner_price_202701": (_utc(2027, 1), _utc(2027, 2)),
        }
    )
    assert health.partition_warnings(cursor, datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == []


@pytest.mark.integration
def test_partition_health_against_real_schema(db):
    from edgecourt.db import repositories

    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    with db.transaction(), db.cursor() as cursor:
        repositories.ensure_partitions(cursor, now)
    report = health.collect_health(db, now=now)
    assert not [w for w in report.warnings if "particion" in w]
    assert report.runner_price_default_rows == 0

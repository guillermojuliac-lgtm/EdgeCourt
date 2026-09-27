"""Auditoria de catalogo (Phase 3.5-C2). Sin red, sin credenciales reales, sin BD.

Fija el contrato del experimento:

* consulta amplia (sin filtro de tipo de mercado) sin alterar el payload del collector;
* un artefacto por slot de 30 min, idempotente, conservando el primer snapshot valido;
* escritura atomica: nunca un run valido a medias;
* la captura jamas abre PostgreSQL; el informe solo en modo lectura;
* --until / --since impiden cualquier llamada fuera de la ventana;
* ningun secreto en ningun artefacto; tiempos en UTC; datos retrasados identificados.
"""

from __future__ import annotations

import ast
import gzip
import json
import stat
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from edgecourt.config import PROJECT_ROOT
from edgecourt.market import catalogue_audit as audit
from edgecourt.market import catalogue_report as report_module
from edgecourt.market.client import MarketFilter

FAKE_PASSWORD = "contrasena-ficticia-de-prueba-c2"
FAKE_APP_KEY = "app-key-ficticia-c2-0123"
FAKE_TOKEN = "token-ficticio-c2-0123456789"

WINDOW_START = datetime(2026, 9, 29, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 19, tzinfo=UTC)
NOW = datetime(2026, 9, 30, 10, 7, tzinfo=UTC)  # slot 10:00


@pytest.fixture
def settings(settings_factory):
    return settings_factory(
        betfair_username="usuario-ficticio",
        betfair_password=FAKE_PASSWORD,
        betfair_app_key=FAKE_APP_KEY,
        betfair_jurisdiction="es",
    )


def _market(market_id: str, competition: str, start: str, *, runners=("A", "B")) -> dict:
    return {
        "marketId": market_id,
        "marketName": "Match Odds",
        "marketStartTime": start,
        "competition": {"id": f"c-{competition}", "name": competition},
        "event": {"id": f"e-{market_id}", "name": " v ".join(runners), "countryCode": "CN"},
        "runners": [
            {"selectionId": i + 1, "runnerName": name, "sortPriority": i + 1}
            for i, name in enumerate(runners)
        ],
    }


def _book(market_id: str, *, delayed=True, total_matched=12.5) -> dict:
    return {
        "marketId": market_id,
        "status": "OPEN",
        "inplay": False,
        "isMarketDataDelayed": delayed,
        "totalMatched": total_matched,
        "numberOfActiveRunners": 2,
        "runners": [
            {
                "selectionId": 1,
                "status": "ACTIVE",
                "ex": {
                    "availableToBack": [{"price": 1.20, "size": 50.0}],
                    "availableToLay": [{"price": 1.25, "size": 30.0}],
                },
            },
            {
                "selectionId": 2,
                "status": "ACTIVE",
                "ex": {
                    "availableToBack": [{"price": 4.8, "size": 10.0}],
                    "availableToLay": [{"price": 6.0, "size": 8.0}],
                },
            },
        ],
    }


class FakeClient:
    """Cliente de lectura simulado: registra cada metodo invocado."""

    def __init__(self, broad=None, match_odds=None, books=None, fail_with=None):
        self.broad = broad if broad is not None else []
        self.match_odds = match_odds if match_odds is not None else list(self.broad)
        self.books = books if books is not None else [_book(m["marketId"]) for m in self.match_odds]
        self.fail_with = fail_with
        self.calls: list[tuple[str, dict]] = []

    def list_market_catalogue(self, market_filter: MarketFilter, *, max_results: int = 200):
        self.calls.append(("list_market_catalogue", market_filter.as_payload()))
        if self.fail_with:
            raise self.fail_with
        return self.match_odds if market_filter.market_type_codes else self.broad

    def list_market_book(self, market_ids):
        self.calls.append(("list_market_book", {"marketIds": list(market_ids)}))
        return [b for b in self.books if b["marketId"] in market_ids]


class ExplodingClient:
    """Cualquier llamada es un fallo del test: se usa para probar que NO hay llamadas."""

    def __getattr__(self, name):
        raise AssertionError(f"no deberia llamarse a Betfair ({name})")


class FakeSessions:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1

    def secret_values(self):
        return frozenset({FAKE_TOKEN, FAKE_APP_KEY, FAKE_PASSWORD})


def _run(settings, out: Path, client, *, now=NOW, sessions=None, **kwargs):
    return audit.run_audit(
        settings,
        out=out,
        since=kwargs.pop("since", WINDOW_START),
        until=kwargs.pop("until", WINDOW_END),
        clock=lambda: now,
        sessions=sessions or FakeSessions(),
        client=client,
        **kwargs,
    )


def _all_artifact_bytes(root: Path) -> bytes:
    blobs = []
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            blobs.append(gzip.decompress(data) if path.suffix == ".gz" else data)
    return b"\n".join(blobs)


CATALOGUE = [
    _market("1.300", "ATP Beijing 2026", "2026-09-30T12:00:00.000Z"),
    _market("1.301", "ATP Beijing 2026", "2026-09-30T14:00:00.000Z"),
]


# --- Consulta Betfair -----------------------------------------------------------


@pytest.mark.critical
def test_broad_payload_contains_no_market_type_codes():
    payload = audit.broad_filter().as_payload()
    assert payload == {"eventTypeIds": ["2"]}
    assert "marketTypeCodes" not in payload
    assert "marketStartTime" not in payload
    assert "competitionIds" not in payload


@pytest.mark.critical
def test_collector_payload_still_contains_match_odds(settings):
    """Regresion: el cambio en MarketFilter no altera el payload del collector."""
    from edgecourt.market.collector import Collector

    now = datetime(2026, 9, 27, 8, 0, tzinfo=UTC)
    collector = Collector(settings, client=object(), sessions=object(), connection=object())
    payload = collector._catalogue_window(now).as_payload()
    assert payload == {
        "eventTypeIds": ["2"],
        "marketTypeCodes": ["MATCH_ODDS"],
        "marketStartTime": {"from": "2026-09-27T08:00:00Z", "to": "2026-09-28T10:00:00Z"},
    }
    assert list(payload) == ["eventTypeIds", "marketTypeCodes", "marketStartTime"]


def test_capture_asks_catalogue_broadly_then_books_for_match_odds(settings, tmp_path):
    client = FakeClient(broad=CATALOGUE)
    _run(settings, tmp_path, client)

    methods = [name for name, _ in client.calls]
    assert methods == ["list_market_catalogue", "list_market_catalogue", "list_market_book"]
    assert client.calls[0][1] == {"eventTypeIds": ["2"]}
    assert client.calls[1][1] == {"eventTypeIds": ["2"], "marketTypeCodes": ["MATCH_ODDS"]}
    assert client.calls[2][1] == {"marketIds": ["1.300", "1.301"]}


@pytest.mark.critical
def test_capture_only_uses_read_operations(settings, tmp_path):
    client = FakeClient(broad=CATALOGUE)
    _run(settings, tmp_path, client)
    assert {name for name, _ in client.calls} <= {"list_market_catalogue", "list_market_book"}


# --- Artefactos -----------------------------------------------------------------


def test_successful_run_writes_all_required_fields(settings, tmp_path):
    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    assert outcome.status == "ok"
    assert outcome.exit_code == 0

    run = json.loads((tmp_path / "2026-09-30" / "1000_run.json").read_text())
    required = {
        "schema_version",
        "run_id",
        "scheduled_slot_utc",
        "captured_at_utc",
        "jurisdiction",
        "endpoint",
        "duration_s",
        "status",
        "catalogue_hash",
        "tennis_market_count",
        "match_odds_count",
        "competition_count",
        "book_market_count",
        "errors",
    }
    assert required <= set(run)
    assert run["run_id"] == "20260930T1000Z"
    assert run["scheduled_slot_utc"] == "2026-09-30T10:00:00Z"
    assert run["jurisdiction"] == "es"
    assert run["tennis_market_count"] == 2
    assert run["match_odds_count"] == 2
    assert run["competition_count"] == 1
    assert run["book_market_count"] == 2
    assert run["errors"] == []
    assert (tmp_path / "catalogues" / f"{run['catalogue_hash']}.json.gz").is_file()
    assert (tmp_path / "2026-09-30" / "1000_books.json.gz").is_file()


def test_artifacts_are_private(settings, tmp_path):
    _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    for path in tmp_path.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_file() and path.name != ".lock":
            assert mode == 0o600, (path, oct(mode))
        if path.is_dir():
            assert mode == 0o700, (path, oct(mode))


def test_empty_run_is_a_valid_observation(settings, tmp_path):
    outcome = _run(settings, tmp_path, FakeClient(broad=[]))
    assert outcome.status == "ok"
    run = outcome.run
    assert run["tennis_market_count"] == 0
    assert run["match_odds_count"] == 0
    assert run["book_market_count"] == 0
    assert run["catalogue_hash"] == audit.catalogue_hash([])
    assert run["market_data_delayed"] is None


def test_identical_catalogue_keeps_the_same_hash():
    reordered = [dict(CATALOGUE[1]), dict(CATALOGUE[0])]
    reordered[0]["runners"] = list(reversed(reordered[0]["runners"]))
    assert audit.catalogue_hash(CATALOGUE) == audit.catalogue_hash(reordered)
    changed = [dict(CATALOGUE[0], marketStartTime="2026-09-30T13:00:00.000Z"), CATALOGUE[1]]
    assert audit.catalogue_hash(changed) != audit.catalogue_hash(CATALOGUE)


def test_each_slot_leaves_evidence_even_if_catalogue_is_unchanged(settings, tmp_path):
    for minutes in (0, 30, 60):
        _run(settings, tmp_path, FakeClient(broad=CATALOGUE), now=NOW + timedelta(minutes=minutes))

    runs = sorted(tmp_path.glob("2026-09-30/*_run.json"))
    assert [p.name for p in runs] == ["1000_run.json", "1030_run.json", "1100_run.json"]
    hashes = {json.loads(p.read_text())["catalogue_hash"] for p in runs}
    assert len(hashes) == 1
    assert len(list((tmp_path / "catalogues").glob("*.json.gz"))) == 1
    assert len(list(tmp_path.glob("2026-09-30/*_books.json.gz"))) == 3


@pytest.mark.critical
def test_repeating_a_slot_does_not_duplicate_and_keeps_the_first(settings, tmp_path):
    _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    run_path = tmp_path / "2026-09-30" / "1000_run.json"
    first = run_path.read_bytes()

    # Mismo slot, catalogo distinto: no debe haber llamadas ni reemplazo.
    outcome = _run(settings, tmp_path, ExplodingClient(), now=NOW + timedelta(minutes=20))

    assert outcome.status == "already_captured"
    assert outcome.exit_code == 0
    assert run_path.read_bytes() == first
    assert len(list(tmp_path.glob("2026-09-30/*_run.json"))) == 1


def test_failed_attempt_is_replaced_by_the_first_valid_one(settings, tmp_path):
    failing = FakeClient(broad=CATALOGUE, fail_with=RuntimeError("red caida"))
    outcome = _run(settings, tmp_path, failing)
    assert outcome.status == "error"
    assert outcome.exit_code == 1

    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE), now=NOW + timedelta(minutes=3))
    assert outcome.status == "ok"
    run = json.loads((tmp_path / "2026-09-30" / "1000_run.json").read_text())
    assert run["status"] == "ok"
    assert run["previous_attempts"][0]["errors"] == ["RuntimeError: red caida"]

    # Y ese primer snapshot valido queda protegido.
    assert _run(settings, tmp_path, ExplodingClient()).status == "already_captured"


# --- Escritura atomica ----------------------------------------------------------


@pytest.mark.critical
def test_failure_while_writing_books_leaves_no_valid_run(settings, tmp_path, monkeypatch):
    real = audit.atomic_write_bytes

    def failing(path, data):
        if path.name.endswith("_books.json.gz"):
            raise OSError("disco lleno")
        return real(path, data)

    monkeypatch.setattr(audit, "atomic_write_bytes", failing)
    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE))

    assert outcome.status == "error"
    run = json.loads((tmp_path / "2026-09-30" / "1000_run.json").read_text())
    assert run["status"] == "error"
    assert not (tmp_path / "2026-09-30" / "1000_books.json.gz").exists()


@pytest.mark.critical
def test_interrupted_process_leaves_no_run_and_no_partial_file(settings, tmp_path, monkeypatch):
    """Un proceso que muere a mitad de escribir no deja ni run.json ni ficheros a medias."""
    import os as real_os

    def dying_replace(src, dst):
        raise KeyboardInterrupt("proceso interrumpido")

    monkeypatch.setattr(audit.os, "replace", dying_replace)
    with pytest.raises(KeyboardInterrupt):
        _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    monkeypatch.setattr(audit.os, "replace", real_os.replace)

    assert not list(tmp_path.rglob("*_run.json"))
    assert not list(tmp_path.rglob(".tmp-*"))
    assert report_module.load_runs(tmp_path, WINDOW_START, WINDOW_END) == []


def test_reader_ignores_corrupt_and_temporary_files(tmp_path):
    day = tmp_path / "2026-09-30"
    day.mkdir()
    (day / "1000_run.json").write_text("{no es json")
    (day / ".tmp-abc_run.json").write_text("{}")
    assert report_module.load_runs(tmp_path, WINDOW_START, WINDOW_END) == []


# --- PostgreSQL -----------------------------------------------------------------


@pytest.mark.critical
def test_capture_never_opens_postgresql(settings, tmp_path, monkeypatch):
    import psycopg

    from edgecourt.db import connection as db_connection

    def forbidden(*args, **kwargs):
        raise AssertionError("la captura C2 no puede conectarse a PostgreSQL")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(db_connection, "connect", forbidden)

    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    assert outcome.status == "ok"


@pytest.mark.critical
def test_capture_module_does_not_import_the_database_layer():
    source = (PROJECT_ROOT / "src" / "edgecourt" / "market" / "catalogue_audit.py").read_text()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {name for name in imported if name.startswith(("edgecourt.db", "psycopg"))}
    forbidden |= {name for name in imported if name == "edgecourt.market.persistence"}
    assert not forbidden, forbidden


@pytest.mark.critical
def test_report_opens_postgresql_only_read_only():
    events: list[str] = []

    class FakeCursor:
        def fetchall(self):
            return [{"market_id": "1.300", "first_seen_at": "2026-09-30T09:00:00Z"}]

    class FakeConnection:
        def __init__(self):
            self._read_only = False

        @property
        def read_only(self):
            return self._read_only

        @read_only.setter
        def read_only(self, value):
            events.append(f"read_only={value}")
            self._read_only = value

        def execute(self, sql, params=None):
            assert self._read_only, "consulta ejecutada sin modo solo lectura"
            assert sql.lstrip().upper().startswith("SELECT")
            events.append("select")
            return FakeCursor()

        def rollback(self):
            events.append("rollback")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    lookup = report_module.collector_lookup_readonly(
        "postgresql:///ficticia", connect=lambda dsn: FakeConnection()
    )
    assert "1.300" in lookup(["1.300", "1.301"])
    assert events == ["read_only=True", "select", "rollback"]


# --- Ventana temporal -----------------------------------------------------------


@pytest.mark.critical
@pytest.mark.parametrize(
    "now",
    [
        WINDOW_END,
        WINDOW_END + timedelta(days=30),
        WINDOW_START - timedelta(minutes=1),
    ],
)
def test_until_and_since_prevent_any_call(settings, tmp_path, now):
    outcome = _run(settings, tmp_path, ExplodingClient(), now=now)
    assert outcome.status == "outside_window"
    assert outcome.exit_code == 0
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_default_window_is_the_official_experiment():
    assert datetime(2026, 9, 29, tzinfo=UTC) == audit.EXPERIMENT_START
    assert datetime(2026, 10, 19, tzinfo=UTC) == audit.EXPERIMENT_END


def test_dry_run_makes_no_calls_and_writes_nothing(settings, tmp_path):
    outcome = _run(settings, tmp_path, ExplodingClient(), dry_run=True)
    assert outcome.status == "dry_run"
    assert outcome.plan["broad_payload"] == {"eventTypeIds": ["2"]}
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


# --- Secretos -------------------------------------------------------------------


@pytest.mark.critical
def test_fake_secrets_never_appear_in_any_artifact(settings, tmp_path):
    _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    leaking = FakeClient(
        broad=CATALOGUE,
        fail_with=RuntimeError(f"fallo con token {FAKE_TOKEN} y clave {FAKE_APP_KEY}"),
    )
    _run(settings, tmp_path, leaking, now=NOW + timedelta(minutes=30))

    blob = _all_artifact_bytes(tmp_path)
    for secret in (FAKE_TOKEN, FAKE_APP_KEY, FAKE_PASSWORD):
        assert secret.encode() not in blob
    error_run = json.loads((tmp_path / "2026-09-30" / "1030_run.json").read_text())
    assert "***" in error_run["errors"][0]


# --- UTC y datos retrasados -----------------------------------------------------


def test_time_calculations_use_utc():
    madrid = timezone(timedelta(hours=2))
    local = datetime(2026, 9, 30, 12, 44, tzinfo=madrid)  # 10:44 UTC
    assert audit.slot_for(local) == datetime(2026, 9, 30, 10, 30, tzinfo=UTC)
    assert audit.iso_utc(local).endswith("Z")
    with pytest.raises(ValueError):
        audit.slot_for(datetime(2026, 9, 30, 10, 44))  # sin zona horaria


def test_run_timestamps_are_utc(settings, tmp_path):
    madrid = timezone(timedelta(hours=2))
    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE), now=NOW.astimezone(madrid))
    assert outcome.run["scheduled_slot_utc"] == "2026-09-30T10:00:00Z"
    assert outcome.run["captured_at_utc"].endswith("Z")


@pytest.mark.critical
def test_delayed_market_data_is_identified(settings, tmp_path):
    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    assert outcome.run["market_data_delayed"] is True

    report = report_module.build_report(
        tmp_path, since=WINDOW_START, until=WINDOW_END, now=NOW + timedelta(hours=1)
    )
    assert {row["market_data_delayed"] for row in report.books} == {True}


def test_mixed_delay_flags_are_reported_as_mixed(settings, tmp_path):
    books = [_book("1.300", delayed=True), _book("1.301", delayed=False)]
    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE, books=books))
    assert outcome.run["market_data_delayed"] == "mixed"


# --- Informe --------------------------------------------------------------------


def test_books_are_reconstructable_per_run(settings, tmp_path):
    outcome = _run(settings, tmp_path, FakeClient(broad=CATALOGUE))
    books = report_module.load_books(tmp_path, outcome.run)
    assert [b["marketId"] for b in books] == ["1.300", "1.301"]

    books_path = tmp_path / "2026-09-30" / "1000_books.json.gz"
    books_path.write_bytes(gzip.compress(b'{"run_id": "otro", "books": []}'))
    with pytest.raises(ValueError):
        report_module.load_books(tmp_path, outcome.run)


def test_spread_ticks_follow_the_betfair_ladder():
    assert report_module.spread_ticks(1.20, 1.25) == 5
    assert report_module.spread_ticks(2.0, 2.02) == 1
    assert report_module.spread_ticks(4.8, 6.0) == 12  # de 4.8 a 6.0 en pasos de 0.1
    assert report_module.spread_ticks(1.5, 1.5) == 0


def test_report_builds_markets_books_and_coverage(settings, tmp_path):
    for minutes in (0, 30):
        _run(settings, tmp_path, FakeClient(broad=CATALOGUE), now=NOW + timedelta(minutes=minutes))

    report = report_module.build_report(
        tmp_path,
        since=datetime(2026, 9, 30, 10, 0, tzinfo=UTC),
        until=datetime(2026, 9, 30, 12, 0, tzinfo=UTC),
        now=datetime(2026, 9, 30, 11, 10, tzinfo=UTC),
        collector_lookup=lambda ids: {"1.300": "visto"},
    )
    assert report.expected == 2  # 10:00 y 10:30 (el slot en curso, 11:00, no cuenta)
    assert report.ok_runs == 2
    assert report.coverage_pct == 100.0

    market = report.markets["1.300"]
    assert market["runs_present"] == 2
    assert market["first_seen_slot"] == "2026-09-30T10:00:00Z"
    assert market["lead_time_hours"] == pytest.approx(1.88, abs=0.01)  # 12:00 - 10:07
    assert market["in_collector"] is True
    assert report.markets["1.301"]["in_collector"] is False

    row = report.books[0]
    assert row["has_back_and_lay"] is True
    assert row["max_spread_ticks"] == 12
    assert row["fav_spread_pct"] == pytest.approx(4.1667, abs=1e-3)
    assert row["top_depth"] == 8.0
    assert row["total_matched_market"] == 12.5

    files = report_module.write_report(report, tmp_path / "informe")
    assert json.loads(files["summary"].read_text())["coverage_pct"] == 100.0
    assert files["books"].read_text().count("\n") == 1 + len(report.books)


# --- CLI ------------------------------------------------------------------------


def test_cli_parses_audit_and_report_commands():
    from edgecourt.cli import _utc_arg, build_parser

    parser = build_parser()
    args = parser.parse_args(
        ["betfair", "catalogue-audit", "--until", "2026-10-19T00:00Z", "--dry-run"]
    )
    assert args.until == datetime(2026, 10, 19, tzinfo=UTC)
    assert args.dry_run is True
    args = parser.parse_args(["betfair", "catalogue-report", "--out", "x", "--no-db"])
    assert args.no_db is True
    assert _utc_arg("2026-09-29") == datetime(2026, 9, 29, tzinfo=UTC)
    assert _utc_arg("2026-09-29T02:00+02:00") == datetime(2026, 9, 29, tzinfo=UTC)

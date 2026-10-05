"""`collector health` debe mostrar instantes en UTC real (defecto detectado el 2026-10-05).

psycopg devuelve los timestamptz en la zona de la SESION (Europe/Madrid) y la CLI
escribia "UTC" sobre esa hora local: una observacion de las 23:40 UTC aparecia
como "01:40:39 UTC", dos horas mas tarde de lo real. Eso dificulto el diagnostico
del incidente de particiones.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from edgecourt import cli
from edgecourt.db import health, repositories

pytest_plugins = ["tests.conftest_db"]

MADRID = ZoneInfo("Europe/Madrid")


# --- Formato ------------------------------------------------------------------


@pytest.mark.critical
def test_format_utc_converts_a_local_instant_to_real_utc():
    madrid = datetime(2026, 10, 1, 1, 40, 39, tzinfo=MADRID)  # CEST, +02:00
    assert cli.format_utc(madrid) == "2026-09-30T23:40:39Z"


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 1, 15, 12, 0, 0, tzinfo=MADRID),  # invierno, +01:00
        datetime(2026, 7, 15, 12, 0, 0, tzinfo=MADRID),  # verano, +02:00
        datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=-8))),
        datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC),
    ],
)
def test_format_utc_is_always_the_same_absolute_instant(moment):
    parsed = datetime.strptime(cli.format_utc(moment), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert parsed == moment.replace(microsecond=0)


def test_format_utc_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        cli.format_utc(datetime(2026, 10, 1, 1, 40, 39))


def test_format_age_variants():
    moment = datetime(2026, 9, 30, 23, 40, 39, tzinfo=MADRID)
    assert cli.format_age(None, 0) == "nunca"
    assert cli.format_age(moment, 5) == "2026-09-30T21:40:39Z  (hace 5 min)"
    assert cli.format_age(moment, 131.5) == "2026-09-30T21:40:39Z  (hace 2.2 h)"


# --- La salida de la CLI ----------------------------------------------------------


@pytest.mark.critical
def test_cli_health_prints_utc_not_local_time(monkeypatch, capsys, settings_factory):
    """Reproduce el defecto: ultima observacion a las 23:40:39 UTC, sesion en Madrid."""
    last = datetime(2026, 9, 30, 23, 40, 39, tzinfo=UTC).astimezone(MADRID)  # 01:40:39+02:00
    report = health.HealthReport(now=datetime(2026, 10, 5, 13, 0, tzinfo=UTC))
    report.last_observation_at = last
    report.last_priced_observation_at = last
    report.observations_total = 3462
    report.warnings.append("la ultima observacion es de hace 109.2 h")

    @contextmanager
    def fake_connection(_settings):
        yield object()

    monkeypatch.setattr(cli, "_db_connection", fake_connection)
    monkeypatch.setattr(health, "collect_health", lambda *a, **k: report)

    code = cli._cmd_collector_health(settings_factory(), argparse.Namespace(stale_minutes=180.0))
    out = capsys.readouterr().out

    assert code == 1
    assert "2026-09-30T23:40:39Z" in out
    assert "01:40:39" not in out  # la hora local ya no se etiqueta como UTC
    assert " UTC " not in out.split("ultima observacion")[1].split("\n")[0]


# --- Con PostgreSQL real y sesion en hora de Madrid -------------------------------------


@pytest.mark.integration
def test_health_against_a_madrid_session_reports_the_true_utc_instant(db):
    from tests.test_partitioning_utc import _insert_obs, _seed_market, _set_tz

    _set_tz(db, "Europe/Madrid")
    _seed_market(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.ensure_partitions(cursor, datetime(2026, 9, 30, tzinfo=UTC))
    _insert_obs(db, datetime(2026, 9, 30, 23, 40, 39, tzinfo=UTC), "ultima")

    report = health.collect_health(db, now=datetime(2026, 10, 5, 13, 0, tzinfo=UTC))

    # psycopg lo devuelve en hora de Madrid...
    assert report.last_observation_at.utcoffset() == timedelta(hours=2)
    # ...y la CLI lo muestra en UTC real.
    assert cli.format_utc(report.last_observation_at) == "2026-09-30T23:40:39Z"
    assert report.partition_default_rows == 0

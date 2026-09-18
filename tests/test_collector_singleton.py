"""Tests de la exclusion mutua entre collectors.

Dos collectors simultaneos no corromperian los datos -las escrituras son
idempotentes- pero duplicarian el consumo de cuota de la API de Betfair.
"""

from __future__ import annotations

import psycopg
import pytest

from edgecourt.db import locks

pytestmark = pytest.mark.integration

pytest_plugins = ["tests.conftest_db"]


def _second_connection(db) -> psycopg.Connection:
    """Otra conexion a la misma base, simulando un segundo proceso.

    Se reconstruye desde la configuracion y no desde `db.info.dsn`, porque
    psycopg omite la contrasena al exponer la cadena de conexion.
    """
    from psycopg.rows import dict_row

    from edgecourt.config import Settings

    return psycopg.connect(Settings().edgecourt_test_dsn, row_factory=dict_row)


@pytest.mark.critical
def test_only_one_process_can_hold_the_lock(db):
    other = _second_connection(db)
    try:
        with (
            locks.singleton(db),
            pytest.raises(locks.LockNotAcquiredError, match="otro collector"),
            locks.singleton(other),
        ):
            pass
    finally:
        other.close()


@pytest.mark.critical
def test_lock_is_released_on_exit(db):
    other = _second_connection(db)
    try:
        with locks.singleton(db):
            pass
        # Liberado: el segundo proceso ya puede tomarlo.
        with locks.singleton(other):
            pass
    finally:
        other.close()


@pytest.mark.critical
def test_lock_is_released_even_if_the_body_fails(db):
    other = _second_connection(db)
    try:
        with pytest.raises(RuntimeError), locks.singleton(db):
            raise RuntimeError("fallo dentro del bloqueo")

        with locks.singleton(other):
            pass
    finally:
        other.close()


def test_error_message_identifies_the_holder(db):
    other = _second_connection(db)
    try:
        with locks.singleton(db):
            with pytest.raises(locks.LockNotAcquiredError) as excinfo:
                locks.try_acquire(other, locks.COLLECTOR_LOCK)
                raise locks.LockNotAcquiredError(
                    str(locks.lock_holder(other, locks.COLLECTOR_LOCK))
                )
            assert "pid" in str(excinfo.value).lower()
    finally:
        other.close()


@pytest.mark.critical
def test_second_collector_refuses_to_run(db, settings_factory):
    """Un collector lanzado a mano no puede solaparse con el del servicio."""
    from edgecourt.market.collector import Collector

    class _Client:
        def list_market_catalogue(self, market_filter, **kwargs):
            return []

    class _Sessions:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    settings = settings_factory()
    settings.ensure_directories()
    other = _second_connection(db)
    try:
        with locks.singleton(db):  # el "servicio" ya esta corriendo
            sessions = _Sessions()
            collector = Collector(settings, client=_Client(), sessions=sessions, connection=other)
            with pytest.raises(locks.LockNotAcquiredError):
                collector.run(max_cycles=1)
            assert sessions.closed, "debe cerrar la sesion aunque no pueda arrancar"
    finally:
        other.close()

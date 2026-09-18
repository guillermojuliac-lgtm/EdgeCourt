"""Fixtures para los tests de integracion con PostgreSQL.

Se omiten automaticamente si no hay `EDGECOURT_TEST_DSN`, de modo que la suite
sigue pasando entera en una maquina sin base de datos.

**Nunca apuntan a la base de produccion**: el nombre debe contener 'test', y hay
una comprobacion que lo exige.
"""

from __future__ import annotations

import os

import pytest

TEST_DSN_ENV = "EDGECOURT_TEST_DSN"


def _dsn() -> str | None:
    return os.environ.get(TEST_DSN_ENV)


@pytest.fixture(scope="session")
def test_dsn() -> str:
    dsn = _dsn()
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} no definido: se omiten los tests de integracion")
    if "test" not in dsn:
        pytest.fail(
            f"{TEST_DSN_ENV} debe apuntar a una base de datos de pruebas "
            "(su nombre debe contener 'test')"
        )
    return dsn


@pytest.fixture
def db(test_dsn):
    """Conexion a una base limpia: se recrea el esquema en cada test."""
    import psycopg
    from psycopg.rows import dict_row

    from edgecourt.db.migrate import migrate

    connection = psycopg.connect(test_dsn, row_factory=dict_row)
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
        connection.commit()
        migrate(connection)
        yield connection
    finally:
        connection.close()

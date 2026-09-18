"""Fixtures para los tests de integracion con PostgreSQL.

Dos cuidados que no son opcionales:

1. **La DSN nunca se expone como argumento de fixture.** pytest imprime los
   argumentos de las fixtures en los tracebacks de error, de modo que una DSN
   recibida como parametro acaba mostrando la contrasena en claro en la salida
   de los tests, en CI y en cualquier registro. Se lee dentro de la fixture y se
   envuelve en un objeto cuyo `repr` esta redactado.

2. **La configuracion se lee de `Settings`, no de `os.environ`.**
   pydantic-settings carga el `.env` para poblar la configuracion pero no
   exporta esas variables al entorno del proceso, asi que leerlas con
   `os.environ` devolvia vacio y los tests se omitian en silencio.
"""

from __future__ import annotations

import pytest

TEST_DSN_SETTING = "EDGECOURT_TEST_DSN"


class SafeDsn:
    """Cadena de conexion que no revela la contrasena al imprimirse.

    Existe porque un `repr` descuidado en un traceback es una via real de fuga:
    basta un test que falle para que la contrasena acabe en la consola.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        from edgecourt.db.connection import redact_dsn

        return f"SafeDsn({redact_dsn(self._value)!r})"

    @property
    def value(self) -> str:
        return self._value


def _resolve_dsn() -> SafeDsn:
    """Obtiene la DSN de pruebas y comprueba que es segura de usar."""
    from edgecourt.config import Settings

    dsn = Settings().edgecourt_test_dsn
    if not dsn:
        pytest.skip(f"{TEST_DSN_SETTING} no configurada: se omiten los tests de integracion")
    if "test" not in dsn:
        pytest.fail(
            f"{TEST_DSN_SETTING} debe apuntar a una base de datos de pruebas "
            "(su nombre debe contener 'test'). Los tests recrean el esquema en cada "
            "prueba y destruirian cualquier dato real."
        )
    return SafeDsn(dsn)


@pytest.fixture
def db():
    """Conexion a una base limpia: el esquema se recrea en cada test.

    No recibe la DSN como argumento a proposito (ver el docstring del modulo).
    """
    import psycopg
    from psycopg.rows import dict_row

    from edgecourt.db.migrate import migrate

    dsn = _resolve_dsn()
    connection = psycopg.connect(dsn.value, row_factory=dict_row, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cursor.execute("CREATE SCHEMA public")

        # Las migraciones necesitan control explicito de transacciones.
        connection.autocommit = False
        migrate(connection)
        yield connection
    finally:
        connection.close()

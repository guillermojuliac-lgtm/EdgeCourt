"""Conexion a PostgreSQL.

Un pool pequeno basta: EdgeCourt son uno o dos procesos de larga duracion, no un
servidor web. Nada de PgBouncer ni de capas intermedias.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from edgecourt.logging_setup import get_logger

log = get_logger("db")

DEFAULT_APPLICATION_NAME = "edgecourt"


class DatabaseNotConfiguredError(RuntimeError):
    """Falta la configuracion de conexion."""


def dsn_from_env(settings: Any = None) -> str:
    """Construye el DSN a partir de la configuracion.

    La contrasena nunca se registra ni se devuelve en mensajes de error.
    """
    if settings is not None and getattr(settings, "database_url", ""):
        return settings.database_url

    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url

    raise DatabaseNotConfiguredError(
        "Falta DATABASE_URL. Definela en el .env, por ejemplo:\n"
        "  DATABASE_URL=postgresql:///edgecourt        (socket local)\n"
        "  DATABASE_URL=postgresql://usuario@localhost:5432/edgecourt"
    )


def redact_dsn(dsn: str) -> str:
    """Version del DSN apta para logs: sin contrasena."""
    if "@" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("://")
    credentials, _, host = rest.rpartition("@")
    user = credentials.split(":", 1)[0] if credentials else ""
    return f"{scheme}://{user}:***@{host}" if user else f"{scheme}://***@{host}"


@contextmanager
def connect(dsn: str, *, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Conexion con filas como diccionarios y cierre garantizado."""
    connection = psycopg.connect(
        dsn,
        row_factory=dict_row,
        autocommit=autocommit,
        application_name=DEFAULT_APPLICATION_NAME,
    )
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(connection: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    """Transaccion explicita: todo o nada.

    El collector la usa por mercado, de modo que nunca quede una observacion que
    declare `has_prices = true` sin sus filas de precio.
    """
    with connection.transaction(), connection.cursor() as cursor:
        yield cursor


def server_version(connection: psycopg.Connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT version() AS v")
        return cursor.fetchone()["v"]

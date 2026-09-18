"""Exclusion mutua entre procesos mediante advisory locks de PostgreSQL.

Un servicio de systemd ya impide arrancar dos copias de la misma unidad, pero no
impide que alguien lance un collector a mano mientras el servicio corre. Dos
collectors simultaneos no corromperian los datos -las escrituras son
idempotentes- pero si duplicarian el consumo de cuota de la API de Betfair y
podrian agotar el limite de peso.

Se usa un advisory lock de sesion en lugar de un fichero PID porque:

* se libera solo si el proceso muere, sin dejar ficheros huerfanos que obliguen
  a limpiar a mano tras un fallo;
* funciona aunque los procesos esten en maquinas distintas, mientras compartan
  la base de datos;
* no necesita permisos de escritura en ningun directorio.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from edgecourt.logging_setup import get_logger

log = get_logger("db.locks")

# Identificadores arbitrarios pero fijos. El primero agrupa los locks de
# EdgeCourt; el segundo distingue cada proceso singleton.
LOCK_NAMESPACE = 0x45444743  # "EDGC"
COLLECTOR_LOCK = 1


class LockNotAcquiredError(RuntimeError):
    """Otro proceso tiene ya el bloqueo."""


def try_acquire(connection: psycopg.Connection, lock_id: int) -> bool:
    """Intenta tomar el bloqueo sin esperar. Devuelve si se consiguio."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s) AS acquired", (LOCK_NAMESPACE, lock_id))
        acquired = bool(cursor.fetchone()["acquired"])
    connection.commit()
    return acquired


def release(connection: psycopg.Connection, lock_id: int) -> None:
    """Libera el bloqueo. Idempotente."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, lock_id))
        connection.commit()
    except psycopg.Error:  # pragma: no cover - la conexion ya no sirve
        pass


def lock_holder(connection: psycopg.Connection, lock_id: int) -> dict | None:
    """Datos del proceso que tiene el bloqueo, para poder decirlo en el error."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.pid, a.application_name, a.backend_start, a.state
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE l.locktype = 'advisory'
              AND l.classid = %s AND l.objid = %s AND l.granted
            LIMIT 1
            """,
            (LOCK_NAMESPACE, lock_id),
        )
        row = cursor.fetchone()
    connection.commit()
    return row


@contextmanager
def singleton(connection: psycopg.Connection, lock_id: int = COLLECTOR_LOCK) -> Iterator[None]:
    """Garantiza que solo un proceso ejecuta este trabajo a la vez."""
    if not try_acquire(connection, lock_id):
        holder = lock_holder(connection, lock_id)
        detail = ""
        if holder:
            detail = (
                f" Lo tiene el proceso PID {holder['pid']} "
                f"({holder['application_name'] or 'sin nombre'}), "
                f"activo desde {holder['backend_start']:%Y-%m-%d %H:%M:%S}."
            )
        raise LockNotAcquiredError(
            "Ya hay otro collector en ejecucion."
            + detail
            + " Comprueba el servicio con: systemctl status edgecourt-collector"
        )

    log.info("bloqueo adquirido", extra={"lock_id": lock_id})
    try:
        yield
    finally:
        release(connection, lock_id)
        log.info("bloqueo liberado", extra={"lock_id": lock_id})

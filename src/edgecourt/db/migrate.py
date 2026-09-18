"""Runner de migraciones.

Ficheros SQL numerados aplicados en orden, con registro de cuales se han
aplicado ya. Sin Alembic a proposito: el DDL se lee tal cual se ejecuta, no hay
autogeneracion que adivine intenciones, y son menos de cien lineas.

Cada migracion se aplica **dentro de una transaccion**, junto con el registro de
que se ha aplicado. Si falla a la mitad, no queda nada a medias ni un registro
mintiendo sobre el estado del esquema.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from edgecourt.config import PROJECT_ROOT
from edgecourt.logging_setup import get_logger

log = get_logger("db.migrate")

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
FILENAME_PATTERN = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version     smallint PRIMARY KEY,
    name        text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Lee las migraciones del disco, ordenadas por version."""
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"No existe el directorio de migraciones: {directory}")

    migrations: list[Migration] = []
    seen: dict[int, str] = {}

    for path in sorted(directory.glob("*.sql")):
        match = FILENAME_PATTERN.match(path.name)
        if not match:
            raise ValueError(
                f"Nombre de migracion invalido: {path.name}. "
                "Formato esperado: NNN_nombre_en_minusculas.sql"
            )
        version = int(match.group(1))
        if version in seen:
            raise ValueError(
                f"Version de migracion duplicada {version}: {seen[version]} y {path.name}"
            )
        seen[version] = path.name
        migrations.append(
            Migration(
                version=version,
                name=match.group(2),
                path=path,
                sql=path.read_text(encoding="utf-8"),
            )
        )

    return migrations


def applied_versions(connection: psycopg.Connection) -> dict[int, str]:
    """Versiones ya aplicadas y su checksum."""
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_TABLE)
        connection.commit()
        cursor.execute("SELECT version, checksum FROM schema_migration ORDER BY version")
        return {row["version"]: row["checksum"] for row in cursor.fetchall()}


def verify_checksums(connection: psycopg.Connection, migrations: list[Migration]) -> None:
    """Comprueba que ninguna migracion aplicada ha cambiado despues.

    Editar una migracion ya aplicada deja el esquema real y el codigo diciendo
    cosas distintas, y el desajuste solo aparece al recrear la base desde cero,
    normalmente en el peor momento.
    """
    applied = applied_versions(connection)
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            raise RuntimeError(
                f"La migracion {migration.version:03d}_{migration.name} ha cambiado desde "
                "que se aplico. Crea una migracion nueva en lugar de editar una existente."
            )


def pending(connection: psycopg.Connection, migrations: list[Migration]) -> list[Migration]:
    applied = applied_versions(connection)
    return [m for m in migrations if m.version not in applied]


def apply(connection: psycopg.Connection, migration: Migration) -> None:
    """Aplica una migracion y registra que se ha aplicado, todo o nada."""
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(migration.sql)
        cursor.execute(
            "INSERT INTO schema_migration (version, name, checksum) VALUES (%s, %s, %s)",
            (migration.version, migration.name, migration.checksum),
        )
    log.info(
        "migracion aplicada",
        extra={"version": migration.version, "name": migration.name},
    )


def migrate(connection: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Aplica todas las migraciones pendientes. Devuelve las aplicadas."""
    migrations = discover(directory)
    verify_checksums(connection, migrations)

    to_apply = pending(connection, migrations)
    for migration in to_apply:
        apply(connection, migration)

    if not to_apply:
        log.info("esquema al dia", extra={"version": max(applied_versions(connection), default=0)})
    return to_apply


def current_version(connection: psycopg.Connection) -> int:
    return max(applied_versions(connection), default=0)

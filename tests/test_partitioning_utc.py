"""Particionado mensual en UTC (incidente del 2026-10-01). PostgreSQL real.

Contexto: la migracion 002 creaba las particiones con fechas sin hora ni zona, que
PostgreSQL interpreta en la zona de la SESION (Europe/Madrid). La de septiembre
termino a las 22:00 UTC del 30-sep, 32 observaciones cayeron en `default`, y el
1-oct a las 00:00 UTC crear octubre fallo por solapamiento: el collector estuvo
~109 horas sin persistir.

La politica es UTC (DEC-019): cada particion es
[YYYY-MM-01 00:00 UTC, mes siguiente 00:00 UTC), sin depender de la zona de la
sesion, del servidor, de Europe/Madrid ni del horario de verano.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest

from edgecourt.db import repositories
from edgecourt.db.migrate import MIGRATIONS_DIR, current_version, discover, migrate

pytestmark = pytest.mark.integration

pytest_plugins = ["tests.conftest_db"]

RUN_ID = uuid.uuid4()
UTC_TZ = UTC


def utc(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC_TZ)


# --- Utilidades ---------------------------------------------------------------


def _set_tz(db, name: str) -> None:
    """Zona horaria de la SESION. Persistente: se confirma la transaccion."""
    db.commit()
    with db.cursor() as cursor:
        cursor.execute(f"SET TIME ZONE '{name}'")
    db.commit()


def _seed_market(db, market_id: str = "1.001") -> None:
    start = utc(2030, 1, 1)
    with db.transaction(), db.cursor() as cursor:
        repositories.upsert_event(
            cursor,
            {
                "event_id": "ev1",
                "event_name": "A v B",
                "competition_id": None,
                "competition_name": "ATP",
                "country_code": "ES",
                "timezone": "GMT",
                "open_date": start,
            },
        )
        repositories.upsert_market(
            cursor,
            {
                "market_id": market_id,
                "event_id": "ev1",
                "market_name": "Match Odds",
                "market_type": "MATCH_ODDS",
                "market_start_time": start,
            },
        )
        for selection_id, name in ((101, "A"), (102, "B")):
            repositories.upsert_runner(
                cursor,
                {
                    "market_id": market_id,
                    "selection_id": selection_id,
                    "runner_name": name,
                    "sort_priority": 1,
                    "handicap": 0,
                },
            )


def _insert_obs(db, observed_at: datetime, capture_key: str, market_id: str = "1.001") -> int:
    runners = []
    for selection_id in (101, 102):
        runner = {
            "selection_id": selection_id,
            "runner_status": "ACTIVE",
            "last_price_traded": None,
            "runner_total_matched": None,
        }
        for column in repositories.PRICE_COLUMNS:
            runner[column] = None
        runner.update(back_price_1=1.72, back_size_1=250.0, lay_price_1=1.76, lay_size_1=180.0)
        runners.append(runner)
    payload = repositories.ObservationPayload(
        market_id=market_id,
        observed_at=observed_at,
        capture_key=capture_key,
        snapshot_label=capture_key,
        minutes_to_start=60.0,
        market_status="OPEN",
        inplay=False,
        bet_delay=0,
        active_runners=2,
        total_matched=0.0,
        collector_run_id=RUN_ID,
        runners=runners,
    )
    with db.transaction(), db.cursor() as cursor:
        return repositories.save_observation(cursor, payload)


def _partition_of(db, observation_id: int) -> str:
    db.commit()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT tableoid::regclass::text AS p FROM market_observation "
            "WHERE observation_id = %s",
            (observation_id,),
        )
        name = cursor.fetchone()["p"]
    db.commit()
    return name


def _bounds(db) -> dict[str, tuple[datetime, datetime]]:
    """Limites reales de las particiones mensuales, como instantes absolutos."""
    db.commit()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname AS name, pb.lower_bound AS lo, pb.upper_bound AS hi
            FROM pg_class c
            JOIN pg_inherits i ON i.inhrelid = c.oid
            JOIN pg_class p ON p.oid = i.inhparent
            CROSS JOIN LATERAL partition_bounds(c.oid) pb
            WHERE p.relname IN ('market_observation', 'runner_price')
            """
        )
        rows = {r["name"]: (r["lo"], r["hi"]) for r in cursor.fetchall()}
    db.commit()
    return rows


def _default_rows(db) -> tuple[int, int]:
    db.commit()
    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) AS n FROM market_observation_default")
        obs = cursor.fetchone()["n"]
        cursor.execute("SELECT count(*) AS n FROM runner_price_default")
        rp = cursor.fetchone()["n"]
    db.commit()
    return obs, rp


def _fingerprint(db) -> dict:
    """Conteo y md5 del contenido de ambas tablas, independiente de la particion."""
    db.commit()
    out = {}
    with db.transaction(), db.cursor() as cursor:
        cursor.execute("SET LOCAL TIME ZONE 'UTC'")
        cursor.execute(
            "SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' "
            "ORDER BY x.observation_id, x.observed_at), '')) AS h FROM market_observation x"
        )
        out["obs"] = tuple(cursor.fetchone().values())
        cursor.execute(
            "SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' "
            "ORDER BY x.observation_id, x.observed_at, x.selection_id), '')) AS h "
            "FROM runner_price x"
        )
        out["rp"] = tuple(cursor.fetchone().values())
    return out


def _utc_month(year: int, month: int) -> tuple[datetime, datetime]:
    nxt_year, nxt_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return utc(year, month, 1), utc(nxt_year, nxt_month, 1)


# --- Politica UTC ---------------------------------------------------------------


@pytest.mark.critical
@pytest.mark.parametrize(
    ("zone", "year", "month"),
    [
        ("UTC", 2030, 1),
        ("Europe/Madrid", 2030, 3),
        ("America/Los_Angeles", 2030, 5),
        ("Asia/Kolkata", 2030, 7),
        ("Pacific/Auckland", 2030, 9),
    ],
)
def test_partition_bounds_are_exact_utc_months_in_any_session_timezone(db, zone, year, month):
    _set_tz(db, zone)
    with db.transaction(), db.cursor() as cursor:
        repositories.ensure_partitions(cursor, utc(year, month, 15))

    bounds = _bounds(db)
    suffix = f"{year}{month:02d}"
    for table in ("market_observation", "runner_price"):
        assert bounds[f"{table}_{suffix}"] == _utc_month(year, month)
        next_year, next_month = (year, month + 1)
        assert bounds[f"{table}_{next_year}{next_month:02d}"] == _utc_month(next_year, next_month)


@pytest.mark.critical
def test_ensure_partitions_creates_current_and_next_month(db):
    with db.transaction(), db.cursor() as cursor:
        names = repositories.ensure_partitions(cursor, utc(2026, 10, 15, 12))

    assert sorted(names) == [
        "market_observation_202610",
        "market_observation_202611",
        "runner_price_202610",
        "runner_price_202611",
    ]
    bounds = _bounds(db)
    assert bounds["market_observation_202611"] == (utc(2026, 11, 1), utc(2026, 12, 1))


def test_ensure_partitions_is_idempotent(db):
    with db.transaction(), db.cursor() as cursor:
        first = repositories.ensure_partitions(cursor, utc(2026, 10, 15))
    before = _bounds(db)
    with db.transaction(), db.cursor() as cursor:
        second = repositories.ensure_partitions(cursor, utc(2026, 10, 31, 23, 59))
    assert first == second
    assert _bounds(db) == before


def test_month_is_chosen_in_utc_not_in_the_local_timezone_of_the_datetime(db):
    """01:30 en Madrid del 1-oct (+02) son las 23:30 UTC del 30-sep: mes de septiembre."""
    from zoneinfo import ZoneInfo

    local = datetime(2026, 10, 1, 1, 30, tzinfo=ZoneInfo("Europe/Madrid"))
    with db.transaction(), db.cursor() as cursor:
        names = repositories.ensure_partitions(cursor, local)
    assert "market_observation_202609" in names
    assert "market_observation_202610" in names


def test_naive_datetime_is_interpreted_as_utc(db):
    with db.transaction(), db.cursor() as cursor:
        names = repositories.ensure_partitions(cursor, datetime(2026, 10, 15, 12))
    assert "market_observation_202610" in names


# --- Cambios de mes, DST y de ano --------------------------------------------------


@pytest.mark.critical
def test_september_to_october_boundary_in_a_madrid_session_never_touches_default(db):
    """Las filas entre 30-sep 22:00 y 1-oct 00:00 UTC, las que se perdieron en produccion."""
    _set_tz(db, "Europe/Madrid")
    _seed_market(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.ensure_partitions(cursor, utc(2026, 9, 15))

    placed = {}
    for key, moment in {
        "sep_21_59": utc(2026, 9, 30, 21, 59),
        "sep_22_10": utc(2026, 9, 30, 22, 10),
        "sep_23_40": utc(2026, 9, 30, 23, 40),
        "sep_23_59_59": utc(2026, 9, 30, 23, 59, 59),
        "oct_00_00": utc(2026, 10, 1, 0, 0),
        "oct_00_05": utc(2026, 10, 1, 0, 5),
    }.items():
        placed[key] = _partition_of(db, _insert_obs(db, moment, key))

    assert placed["sep_21_59"] == "market_observation_202609"
    assert placed["sep_22_10"] == "market_observation_202609"
    assert placed["sep_23_40"] == "market_observation_202609"
    assert placed["sep_23_59_59"] == "market_observation_202609"
    assert placed["oct_00_00"] == "market_observation_202610"
    assert placed["oct_00_05"] == "market_observation_202610"
    assert _default_rows(db) == (0, 0)


@pytest.mark.critical
def test_october_can_be_created_even_with_rows_from_september_utc_in_default(db):
    """El estado que rompio produccion: filas de 22:10-23:40 UTC del 30-sep en default."""
    _set_tz(db, "Europe/Madrid")
    _seed_market(db)
    # Sin particion de septiembre, esas filas caen en default.
    with db.transaction(), db.cursor() as cursor:
        repositories.ensure_partitions(cursor, utc(2026, 10, 1, 0, 0))  # octubre y noviembre
    in_default = [
        _partition_of(db, _insert_obs(db, utc(2026, 9, 30, 22, minute), f"k{minute}"))
        for minute in (10, 50)
    ]
    assert in_default == ["market_observation_default"] * 2

    # Con limites UTC, octubre no solapa con ellas: crearlo ya no falla.
    with db.transaction(), db.cursor() as cursor:
        names = repositories.ensure_partitions(cursor, utc(2026, 10, 1, 0, 0, 42))
    assert "market_observation_202610" in names
    assert _bounds(db)["market_observation_202610"] == (utc(2026, 10, 1), utc(2026, 11, 1))


@pytest.mark.critical
def test_dst_changes_do_not_move_partition_bounds(db):
    """Fin del horario de verano europeo (25-oct-2026 a las 01:00 UTC) y su inicio (29-mar)."""
    _set_tz(db, "Europe/Madrid")
    _seed_market(db)
    with db.transaction(), db.cursor() as cursor:
        repositories.ensure_partitions(cursor, utc(2026, 3, 10))
        repositories.ensure_partitions(cursor, utc(2026, 10, 10))

    bounds = _bounds(db)
    assert bounds["market_observation_202610"] == (utc(2026, 10, 1), utc(2026, 11, 1))
    assert bounds["market_observation_202603"] == (utc(2026, 3, 1), utc(2026, 4, 1))

    cases = {
        "oct25_0030": (utc(2026, 10, 25, 0, 30), "market_observation_202610"),
        "oct25_0130": (utc(2026, 10, 25, 1, 30), "market_observation_202610"),
        "oct31_2330": (utc(2026, 10, 31, 23, 30), "market_observation_202610"),
        "nov01_0000": (utc(2026, 11, 1, 0, 0), "market_observation_202611"),
        "mar29_0030": (utc(2026, 3, 29, 0, 30), "market_observation_202603"),
        "mar29_0130": (utc(2026, 3, 29, 1, 30), "market_observation_202603"),
    }
    for key, (moment, expected) in cases.items():
        assert _partition_of(db, _insert_obs(db, moment, key)) == expected, key
    assert _default_rows(db) == (0, 0)


@pytest.mark.critical
def test_december_to_january_year_change(db):
    _set_tz(db, "Europe/Madrid")
    _seed_market(db)
    with db.transaction(), db.cursor() as cursor:
        names = repositories.ensure_partitions(cursor, utc(2026, 12, 15))
    assert "market_observation_202612" in names
    assert "market_observation_202701" in names

    bounds = _bounds(db)
    assert bounds["market_observation_202612"] == (utc(2026, 12, 1), utc(2027, 1, 1))
    assert bounds["market_observation_202701"] == (utc(2027, 1, 1), utc(2027, 2, 1))
    assert _partition_of(db, _insert_obs(db, utc(2026, 12, 31, 23, 59, 59), "dec")) == (
        "market_observation_202612"
    )
    assert _partition_of(db, _insert_obs(db, utc(2027, 1, 1, 0, 0, 0), "jan")) == (
        "market_observation_202701"
    )
    assert _default_rows(db) == (0, 0)


def test_fresh_database_has_current_and_next_month_partitions(db):
    """La migracion 005 deja creadas las particiones del mes actual y del siguiente (UTC)."""
    now = datetime.now(UTC)
    nxt = date(now.year + (now.month == 12), now.month % 12 + 1, 1)
    bounds = _bounds(db)
    assert f"market_observation_{now.year}{now.month:02d}" in bounds
    assert f"runner_price_{nxt.year}{nxt.month:02d}" in bounds


def test_warning_when_an_existing_partition_has_non_utc_bounds(db):
    """ensure_month_partition no falla ante un limite heredado: avisa."""
    _set_tz(db, "Europe/Madrid")
    with db.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE market_observation_203001 PARTITION OF market_observation "
            "FOR VALUES FROM ('2030-01-01 00:00:00+01') TO ('2030-02-01 00:00:00+01')"
        )
    db.commit()
    messages: list[str] = []
    db.add_notice_handler(lambda diag: messages.append(diag.message_primary))
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT ensure_month_partition('market_observation', DATE '2030-01-01') AS n"
        )
        assert cursor.fetchone()["n"] == "market_observation_203001"
    db.commit()
    assert any("limites UTC esperados" in m for m in messages)


# --- El incidente real: esquema 001-004 con limites de Madrid y filas atrapadas ---


def _build_legacy_database(db, tmp_path):
    """Esquema de produccion antes de la reparacion: migraciones 001-004 con la funcion
    antigua, septiembre con limites de Madrid y filas atrapadas en default."""
    with db.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")
    db.commit()

    old = tmp_path / "old"
    old.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("00[1-4]_*.sql")):
        shutil.copy(path, old / path.name)
    migrate(db, old)
    assert current_version(db) == 4

    _set_tz(db, "Europe/Madrid")
    with db.cursor() as cursor:
        for table in ("market_observation", "runner_price"):
            # Funcion antigua: fechas sin zona, interpretadas en la sesion.
            cursor.execute("SELECT ensure_month_partition(%s, DATE '2026-09-01')", (table,))
    db.commit()
    _seed_market(db)


def _legacy_rows(db) -> dict[str, int]:
    ids = {}
    for key, moment in {
        "sep18_a": utc(2026, 9, 18, 12, 0),
        "sep18_b": utc(2026, 9, 18, 12, 1),
        "sep30_2130": utc(2026, 9, 30, 21, 30),
        "sep30_2210": utc(2026, 9, 30, 22, 10),  # atrapadas en default en produccion
        "sep30_2250": utc(2026, 9, 30, 22, 50),
        "sep30_2320": utc(2026, 9, 30, 23, 20),
        "sep30_2340": utc(2026, 9, 30, 23, 40),
    }.items():
        ids[key] = _insert_obs(db, moment, key)
    return ids


@pytest.mark.critical
def test_the_production_incident_is_reproduced_exactly(db, tmp_path):
    _build_legacy_database(db, tmp_path)
    ids = _legacy_rows(db)

    # Las filas de 22:10-23:40 UTC quedaron en default, sin error.
    assert _partition_of(db, ids["sep30_2130"]) == "market_observation_202609"
    for key in ("sep30_2210", "sep30_2250", "sep30_2320", "sep30_2340"):
        assert _partition_of(db, ids[key]) == "market_observation_default"
    assert _default_rows(db) == (4, 8)

    # Y crear octubre (lo que hacia el collector a las 00:00 UTC) falla igual que en produccion.
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="default partition"),
        db.transaction(),
        db.cursor() as cursor,
    ):
        cursor.execute("SELECT ensure_month_partition('market_observation', DATE '2026-10-01')")
    db.rollback()


@pytest.mark.critical
def test_migration_005_repairs_production_state_without_losing_data(db, tmp_path):
    _build_legacy_database(db, tmp_path)
    ids = _legacy_rows(db)
    before = _fingerprint(db)
    assert before["obs"][0] == 7
    assert before["rp"][0] == 14
    assert _default_rows(db) == (4, 8)

    applied = migrate(db)

    assert [m.version for m in applied] == [5]
    # ANTES = DESPUES: mismas filas, mismo contenido, mismos identificadores.
    assert _fingerprint(db) == before
    # Nada atrapado en default y septiembre pasa a ser el mes UTC exacto.
    assert _default_rows(db) == (0, 0)
    assert _bounds(db)["market_observation_202609"] == (utc(2026, 9, 1), utc(2026, 10, 1))
    assert _bounds(db)["runner_price_202609"] == (utc(2026, 9, 1), utc(2026, 10, 1))
    for key, observation_id in ids.items():
        assert _partition_of(db, observation_id) == "market_observation_202609", key

    # Octubre (y noviembre, por adelantado) ya pueden crearse, incluso en sesion Madrid.
    with db.transaction(), db.cursor() as cursor:
        names = repositories.ensure_partitions(cursor, utc(2026, 10, 1, 0, 0, 42))
    assert {"market_observation_202610", "market_observation_202611"} <= set(names)
    new_id = _insert_obs(db, utc(2026, 10, 1, 0, 5), "oct")
    assert _partition_of(db, new_id) == "market_observation_202610"
    assert new_id > max(ids.values())  # la secuencia de identidad no retrocedio
    assert _default_rows(db) == (0, 0)


@pytest.mark.critical
def test_migration_005_keeps_relations_indexes_and_constraints(db, tmp_path):
    _build_legacy_database(db, tmp_path)
    ids = _legacy_rows(db)
    migrate(db)

    with db.cursor() as cursor:
        # observacion -> precios: cada observacion conserva sus 2 precios.
        cursor.execute(
            "SELECT o.observation_id, count(p.selection_id) AS n FROM market_observation o "
            "LEFT JOIN runner_price p USING (observation_id, observed_at) GROUP BY 1"
        )
        assert {r["observation_id"]: r["n"] for r in cursor.fetchall()} == {
            observation_id: 2 for observation_id in ids.values()
        }
        # indices de la particion reajustada
        cursor.execute(
            "SELECT count(*) AS n FROM pg_indexes WHERE tablename = 'market_observation_202609'"
        )
        assert cursor.fetchone()["n"] >= 4
        # claves foraneas hacia la particion reajustada (lado referenciado)
        cursor.execute(
            "SELECT count(*) AS n FROM pg_constraint "
            "WHERE contype = 'f' AND confrelid = 'market_observation_202609'::regclass"
        )
        assert cursor.fetchone()["n"] >= 1
    db.commit()

    # Las restricciones siguen funcionando.
    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        db.transaction(),
        db.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO runner_price (observation_id, observed_at, selection_id, runner_status) "
            "VALUES (999999, %s, 101, 'ACTIVE')",
            (utc(2026, 9, 18, 12, 0),),
        )
    db.rollback()
    # La idempotencia por (market_id, capture_key, observed_at) tambien.
    again = _insert_obs(db, utc(2026, 9, 30, 22, 10), "sep30_2210")
    assert again == ids["sep30_2210"]


@pytest.mark.critical
def test_migration_005_rolls_back_completely_if_a_check_fails(db, tmp_path):
    """Una fila de la particion antigua que no cabe en el mes UTC exige intervencion
    manual: la migracion aborta y NO cambia nada."""
    _build_legacy_database(db, tmp_path)
    _legacy_rows(db)
    # 31-ago 22:30 UTC: cabe en septiembre-Madrid, pero es agosto en UTC.
    _insert_obs(db, utc(2026, 8, 31, 22, 30), "aug31")
    before = _fingerprint(db)
    legacy_bounds_default = _default_rows(db)

    with pytest.raises(psycopg.errors.RaiseException, match="intervencion manual"):
        migrate(db)
    db.rollback()

    assert current_version(db) == 4  # la migracion no se registro
    assert _fingerprint(db) == before
    assert _default_rows(db) == legacy_bounds_default
    db.commit()
    with db.cursor() as cursor:
        cursor.execute("SELECT to_regclass('utc_month_bounds') AS f")
        # La funcion nueva se revirtio junto con el resto.
        assert cursor.fetchone()["f"] is None
    db.commit()


def test_migration_005_is_idempotent_on_an_already_correct_database(db):
    """Reaplicar el SQL sobre un esquema ya correcto no cambia nada ni falla."""
    migration = next(m for m in discover() if m.version == 5)
    before_bounds = _bounds(db)
    before = _fingerprint(db)
    with db.transaction(), db.cursor() as cursor:
        cursor.execute(migration.sql)
    assert _bounds(db) == before_bounds
    assert _fingerprint(db) == before


@pytest.mark.critical
def test_migration_005_repairs_adjacent_legacy_partitions(db, tmp_path):
    """Septiembre y octubre con limites de Madrid (p. ej. el codigo nuevo creo el mes
    siguiente con la funcion antigua): reajustarlos uno a uno solaparia."""
    _build_legacy_database(db, tmp_path)
    with db.cursor() as cursor:
        for table in ("market_observation", "runner_price"):
            cursor.execute("SELECT ensure_month_partition(%s, DATE '2026-10-01')", (table,))
    db.commit()
    for key, moment in {
        "sep18": utc(2026, 9, 18, 12),
        "sep30": utc(2026, 9, 30, 21, 30),
        "oct01": utc(2026, 10, 1, 3, 0),
        "oct20": utc(2026, 10, 20, 12, 0),
    }.items():
        _insert_obs(db, moment, key)
    before = _fingerprint(db)

    migrate(db)

    assert _fingerprint(db) == before
    bounds = _bounds(db)
    assert bounds["market_observation_202609"] == (utc(2026, 9, 1), utc(2026, 10, 1))
    assert bounds["market_observation_202610"] == (utc(2026, 10, 1), utc(2026, 11, 1))
    assert bounds["runner_price_202610"] == (utc(2026, 10, 1), utc(2026, 11, 1))
    assert _default_rows(db) == (0, 0)

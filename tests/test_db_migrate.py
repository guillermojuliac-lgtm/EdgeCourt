"""Tests del runner de migraciones que no requieren PostgreSQL."""

from __future__ import annotations

import re

import pytest

from edgecourt.db.connection import (
    DatabaseNotConfiguredError,
    dsn_from_env,
    redact_dsn,
)
from edgecourt.db.migrate import discover


def test_migrations_are_discovered_in_order():
    migrations = discover()
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)
    assert migrations[0].version == 1


def test_every_migration_has_a_checksum():
    for migration in discover():
        assert len(migration.checksum) == 64
        assert migration.sql.strip()


def test_checksum_changes_with_content(tmp_path):
    (tmp_path / "001_uno.sql").write_text("SELECT 1;")
    first = discover(tmp_path)[0].checksum
    (tmp_path / "001_uno.sql").write_text("SELECT 2;")
    assert discover(tmp_path)[0].checksum != first


def test_invalid_filename_is_rejected(tmp_path):
    (tmp_path / "primera.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="Nombre de migracion invalido"):
        discover(tmp_path)


def test_duplicate_version_is_rejected(tmp_path):
    (tmp_path / "001_uno.sql").write_text("SELECT 1;")
    (tmp_path / "001_dos.sql").write_text("SELECT 2;")
    with pytest.raises(ValueError, match="duplicada"):
        discover(tmp_path)


def test_missing_directory_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError):
        discover(tmp_path / "no-existe")


# --- Contenido del esquema ----------------------------------------------------


def _all_sql() -> str:
    return "\n".join(m.sql for m in discover())


def _normalised_sql() -> str:
    """SQL con espacios colapsados, para que los tests no dependan del formato."""
    return re.sub(r"\s+", " ", _all_sql())


@pytest.mark.critical
def test_schema_separates_observation_from_prices():
    """La distincion observacion / precios es el nucleo del diseno."""
    sql = _all_sql()
    assert "CREATE TABLE market_observation" in sql
    assert "CREATE TABLE runner_price" in sql
    assert "has_prices" in sql
    assert "has_liquidity" in sql


@pytest.mark.critical
def test_schema_enforces_liquidity_implies_prices():
    """No puede haber liquidez sin precios: seria incoherente."""
    assert "observation_liquidity_implies_prices" in _all_sql()


@pytest.mark.critical
def test_schema_has_idempotency_index():
    """Sin este indice unico, el collector duplicaria al reintentar."""
    sql = _all_sql()
    assert "market_observation_capture" in sql
    assert "market_id, capture_key, observed_at" in sql


@pytest.mark.critical
def test_ledger_is_protected_against_updates():
    """paper_bet y bet_settlement solo admiten INSERT (brief §15)."""
    sql = _all_sql()
    for table in ("paper_bet", "bet_settlement"):
        assert f"RULE {table}_no_update" in sql
        assert f"RULE {table}_no_delete" in sql


@pytest.mark.critical
def test_settlement_is_a_separate_table():
    """Separarla es lo que hace inmutable el registro de la apuesta."""
    sql = _normalised_sql()
    assert "CREATE TABLE bet_settlement" in sql
    assert "paper_bet_id bigint PRIMARY KEY REFERENCES paper_bet" in sql


@pytest.mark.critical
def test_paper_bet_freezes_the_market_book():
    """Una apuesta debe poder auditarse aunque su snapshot se haya archivado."""
    assert "market_book_snapshot jsonb NOT NULL" in _all_sql()


@pytest.mark.critical
def test_prediction_has_no_hard_fk_to_observation():
    """Una FK dura impediria purgar observaciones a los 90 dias."""
    sql = _all_sql()
    sql = _normalised_sql()
    prediction_block = sql[
        sql.index("CREATE TABLE prediction") : sql.index("CREATE INDEX prediction_by_model")
    ]
    assert "observation_id bigint NOT NULL," in prediction_block
    assert "REFERENCES market_observation" not in prediction_block


@pytest.mark.critical
def test_only_one_production_model_is_possible():
    """La base de datos lo impone; no depende de que nadie se acuerde."""
    sql = _all_sql()
    assert "model_version_single_production" in sql
    assert "WHERE slot = 'production'" in sql


@pytest.mark.critical
def test_purge_requires_verified_archive():
    """Ninguna observacion se borra sin exportacion a Parquet verificada."""
    sql = _all_sql()
    assert "CREATE TABLE archive_run" in sql
    assert "archive_verified_requires_matching_counts" in sql
    assert "archive_purge_requires_verification" in sql
    assert "assert_archived" in sql


def test_player_id_stays_nullable_until_phase_8b():
    sql = _normalised_sql()
    assert "player_id text," in sql
    assert "NULL hasta PHASE 8b" in sql


def test_tables_are_partitioned_for_archiving():
    sql = _all_sql()
    assert sql.count("PARTITION BY RANGE (observed_at)") == 2
    assert "ensure_month_partition" in sql


def test_analysis_views_exist():
    sql = _all_sql()
    assert "CREATE VIEW liquidity_emergence" in sql
    assert "CREATE VIEW snapshot_coverage" in sql


# --- Conexion -----------------------------------------------------------------


def test_missing_database_url_fails_with_guidance(monkeypatch, settings_factory):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DatabaseNotConfiguredError, match="DATABASE_URL"):
        dsn_from_env(settings_factory())


def test_dsn_comes_from_settings(settings_factory):
    settings = settings_factory(database_url="postgresql:///edgecourt")
    assert dsn_from_env(settings) == "postgresql:///edgecourt"


@pytest.mark.critical
def test_redact_dsn_hides_the_password():
    dsn = "postgresql://willy:contrasena-secreta@localhost:5432/edgecourt"
    redacted = redact_dsn(dsn)
    assert "contrasena-secreta" not in redacted
    assert "willy" in redacted
    assert "localhost:5432/edgecourt" in redacted


def test_redact_dsn_handles_socket_urls():
    assert redact_dsn("postgresql:///edgecourt") == "postgresql:///edgecourt"


@pytest.mark.critical
def test_migrations_must_not_control_transactions(tmp_path):
    """Una migracion con COMMIT propio rompe la atomicidad del runner.

    Regresion: los ficheros incluian BEGIN/COMMIT, y ese COMMIT cerraba la
    transaccion que abre el runner, invalidando el savepoint de psycopg y
    dejando el registro en schema_migration fuera de la operacion atomica.
    """
    (tmp_path / "001_mala.sql").write_text("BEGIN;\nCREATE TABLE x (i int);\nCOMMIT;\n")
    with pytest.raises(ValueError, match="control de transaccion"):
        discover(tmp_path)


@pytest.mark.critical
def test_shipped_migrations_have_no_transaction_control():
    for migration in discover():
        assert "BEGIN;" not in migration.sql, migration.name
        assert "COMMIT;" not in migration.sql, migration.name


def test_rollback_is_also_rejected(tmp_path):
    (tmp_path / "001_mala.sql").write_text("CREATE TABLE x (i int);\nROLLBACK;\n")
    with pytest.raises(ValueError, match="ROLLBACK"):
        discover(tmp_path)

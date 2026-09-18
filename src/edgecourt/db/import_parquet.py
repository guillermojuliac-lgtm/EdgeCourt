"""Migracion de los snapshots ya almacenados en Parquet a PostgreSQL.

Requisitos de la migracion:

* **Sin perdida.** Cada fila del Parquet debe acabar representada en PostgreSQL,
  incluidas las observaciones **sin precios**, que son informacion valida: un
  mercado OPEN con runners ACTIVE y sin BACK/LAY es justo lo que permite medir
  cuando aparece la liquidez.
* **Sin duplicados.** Usa las mismas claves unicas que el collector, asi que
  reejecutarla es inocuo. Si el collector ya escribio una observacion, la
  migracion la reconoce y no la duplica.
* **No destructiva.** Los Parquet originales no se tocan ni se borran.

El esquema plano del Parquet (37 columnas, una fila por runner y captura) se
descompone en el modelo relacional: evento, mercado, runner, observacion y
precios.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg

from edgecourt.db import repositories
from edgecourt.logging_setup import get_logger
from edgecourt.storage import read_parquet

log = get_logger("db.import")

# Identificador fijo para distinguir en la base lo migrado de lo recolectado en vivo.
MIGRATION_RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@dataclass(slots=True)
class ImportReport:
    """Recuento de lo migrado, para poder verificar que no se perdio nada."""

    parquet_rows: int = 0
    distinct_snapshots: int = 0
    events: int = 0
    markets: int = 0
    runners: int = 0
    observations: int = 0
    observations_with_prices: int = 0
    observations_without_prices: int = 0
    runner_prices: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "parquet_rows": self.parquet_rows,
            "distinct_snapshots": self.distinct_snapshots,
            "events": self.events,
            "markets": self.markets,
            "runners": self.runners,
            "observations": self.observations,
            "observations_with_prices": self.observations_with_prices,
            "observations_without_prices": self.observations_without_prices,
            "runner_prices": self.runner_prices,
            "skipped": len(self.skipped),
        }


def _value(row: pd.Series, column: str) -> Any:
    """Valor limpio de una celda: los NaN de pandas pasan a None."""
    if column not in row.index:
        return None
    value = row[column]
    if value is None or (isinstance(value, float) and value != value) or pd.isna(value):
        return None
    return value


def _event_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "event_id": str(_value(row, "event_id")),
        "event_name": str(_value(row, "event_name") or "(desconocido)"),
        "competition_id": None,
        "competition_name": _value(row, "competition_name"),
        "country_code": _value(row, "country_code"),
        "timezone": _value(row, "timezone"),
        "open_date": _value(row, "market_start_time"),
    }


def _market_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "market_id": str(_value(row, "market_id")),
        "event_id": str(_value(row, "event_id")),
        "market_name": str(_value(row, "market_name") or "Match Odds"),
        "market_type": "MATCH_ODDS",
        "market_start_time": _value(row, "market_start_time"),
    }


def _runner_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "market_id": str(_value(row, "market_id")),
        "selection_id": int(_value(row, "selection_id")),
        "runner_name": str(_value(row, "runner_name") or "(desconocido)"),
        "sort_priority": _value(row, "sort_priority"),
        "handicap": 0,
        # player_id se deja intacto: lo rellenara PHASE 8b.
    }


def _runner_price_payload(row: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "selection_id": int(_value(row, "selection_id")),
        "runner_status": _value(row, "runner_status") or "UNKNOWN",
        "last_price_traded": _value(row, "last_price_traded"),
        "runner_total_matched": _value(row, "runner_total_matched"),
    }
    for column in repositories.PRICE_COLUMNS:
        payload[column] = _value(row, column)
    return payload


def import_snapshots(
    connection: psycopg.Connection,
    odds_dir: Path,
    *,
    dataset: str = "betfair_match_odds",
) -> ImportReport:
    """Migra los Parquet de snapshots a PostgreSQL. Idempotente y no destructiva."""
    source = Path(odds_dir) / dataset
    report = ImportReport()

    if not source.exists():
        log.info("no hay Parquet que migrar", extra={"path": str(source)})
        return report

    frame = read_parquet(source)
    report.parquet_rows = len(frame)
    if frame.empty:
        return report

    report.distinct_snapshots = int(frame["snapshot_id"].nunique())

    # Una observacion por (mercado, etiqueta): es la clave natural del Parquet
    # actual, donde cada fila es un runner dentro de una captura.
    grouped = frame.groupby(["market_id", "snapshot_label"], dropna=True, sort=True)

    events_seen: set[str] = set()
    markets_seen: set[str] = set()
    runners_seen: set[tuple[str, int]] = set()

    for (market_id, snapshot_label), group in grouped:
        first = group.iloc[0]

        if _value(first, "event_id") is None:
            report.skipped.append(f"{market_id}:{snapshot_label} (sin event_id)")
            continue

        # Cada observacion, con su catalogo, en una sola transaccion.
        with connection.transaction(), connection.cursor() as cursor:
            event = _event_payload(first)
            repositories.upsert_event(cursor, event)
            events_seen.add(event["event_id"])

            market = _market_payload(first)
            repositories.upsert_market(cursor, market)
            markets_seen.add(market["market_id"])

            for _, runner_row in group.iterrows():
                runner = _runner_payload(runner_row)
                repositories.upsert_runner(cursor, runner)
                runners_seen.add((runner["market_id"], runner["selection_id"]))

            observed_at = _value(first, "observed_at")
            repositories.ensure_partitions(cursor, observed_at)

            payload = repositories.ObservationPayload(
                market_id=str(market_id),
                observed_at=observed_at,
                capture_key=str(snapshot_label),
                snapshot_label=str(snapshot_label),
                minutes_to_start=float(_value(first, "minutes_to_start") or 0.0),
                market_status=str(_value(first, "market_status") or "UNKNOWN"),
                inplay=bool(_value(first, "inplay")),
                bet_delay=_value(first, "bet_delay"),
                active_runners=_value(first, "number_of_active_runners"),
                total_matched=_value(first, "total_matched"),
                collector_run_id=MIGRATION_RUN_ID,
                runners=[_runner_price_payload(r) for _, r in group.iterrows()],
            )
            repositories.save_observation(cursor, payload)

            report.observations += 1
            if payload.has_prices:
                report.observations_with_prices += 1
                report.runner_prices += payload.runners_with_prices
            else:
                report.observations_without_prices += 1

    report.events = len(events_seen)
    report.markets = len(markets_seen)
    report.runners = len(runners_seen)

    log.info("migracion de Parquet completada", extra=report.as_dict())
    return report


def verify_import(
    connection: psycopg.Connection, odds_dir: Path, *, dataset: str = "betfair_match_odds"
) -> dict[str, Any]:
    """Comprueba que cada captura del Parquet tiene su observacion en PostgreSQL.

    Devuelve un informe; no lanza excepciones. Los Parquet originales siguen
    intactos, asi que una discrepancia se puede investigar sin prisa.
    """
    source = Path(odds_dir) / dataset
    if not source.exists():
        return {"parquet_exists": False, "ok": True}

    frame = read_parquet(source)
    expected = {
        (str(m), str(label))
        for m, label in zip(frame["market_id"], frame["snapshot_label"], strict=True)
    }

    with connection.cursor() as cursor:
        cursor.execute("SELECT market_id, capture_key FROM market_observation")
        found = {(row["market_id"], row["capture_key"]) for row in cursor.fetchall()}

        cursor.execute("SELECT count(*) AS n FROM market_observation")
        observations = cursor.fetchone()["n"]
        cursor.execute("SELECT count(*) AS n FROM runner_price")
        prices = cursor.fetchone()["n"]

    missing = sorted(expected - found)
    return {
        "parquet_exists": True,
        "parquet_rows": len(frame),
        "expected_observations": len(expected),
        "observations_in_db": observations,
        "runner_prices_in_db": prices,
        "missing": missing,
        "ok": not missing,
    }

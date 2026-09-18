"""Exportacion de PostgreSQL a Parquet.

Invertido el sentido respecto al diseno anterior: PostgreSQL es ahora la fuente
de verdad operativa y Parquet el formato analitico e historico. El collector ya
no escribe Parquet; se genera desde la base de datos cuando hace falta.

La exportacion **no borra nada**. La purga posterior es un paso separado que,
ademas, exige una fila verificada en `archive_run` (ver migracion 003): sin
exportacion comprobada no se puede eliminar ninguna observacion.

Formato de salida: una fila por observacion y runner, con las columnas de precio
aplanadas. Es el formato que consumen DuckDB y los cuadernos de analisis, y
mantiene la compatibilidad con lo que ya habia en `data/odds/`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg

from edgecourt.db.repositories import PRICE_COLUMNS
from edgecourt.logging_setup import get_logger
from edgecourt.storage import write_partitioned_parquet

log = get_logger("db.export")

SNAPSHOTS_DATASET = "betfair_match_odds"

# Se exporta la observacion completa, incluidas las que no tienen precios: son
# las que permiten medir cuando aparece la liquidez.
EXPORT_QUERY = """
SELECT
    o.observation_id,
    o.observed_at,
    to_char(o.observed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS date,
    o.capture_key,
    o.snapshot_label,
    o.minutes_to_start,
    o.market_id,
    m.market_name,
    m.market_start_time,
    o.market_status,
    o.inplay,
    o.bet_delay,
    o.active_runners,
    o.total_matched,
    o.has_prices,
    o.has_liquidity,
    o.runners_with_prices,
    o.total_available,
    o.best_back_available,
    o.best_lay_available,
    o.max_spread_pct,
    e.event_id,
    e.event_name,
    e.competition_name,
    e.country_code,
    e.timezone,
    p.selection_id,
    rn.runner_name,
    rn.sort_priority,
    p.runner_status,
    p.runner_total_matched,
    p.last_price_traded,
    {price_columns}
FROM market_observation o
JOIN betfair_market m USING (market_id)
JOIN betfair_event e USING (event_id)
LEFT JOIN runner_price p
       ON p.observation_id = o.observation_id AND p.observed_at = o.observed_at
LEFT JOIN betfair_runner rn
       ON rn.market_id = o.market_id AND rn.selection_id = p.selection_id
WHERE o.observed_at >= %(start)s AND o.observed_at < %(end)s
ORDER BY o.observed_at, o.market_id, p.selection_id
"""


@dataclass(slots=True)
class ExportResult:
    rows: int
    observations: int
    with_prices: int
    without_prices: int
    destination: Path | None
    files: list[Path]
    sha256: str = ""
    bytes_written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "observations": self.observations,
            "with_prices": self.with_prices,
            "without_prices": self.without_prices,
            "destination": str(self.destination) if self.destination else None,
            "files": len(self.files),
            "bytes": self.bytes_written,
        }


def _query() -> str:
    columns = ",\n    ".join(f"p.{column}" for column in PRICE_COLUMNS)
    return EXPORT_QUERY.format(price_columns=columns)


def export_range(
    connection: psycopg.Connection,
    odds_dir: Path,
    *,
    start: date,
    end: date,
    dataset: str = SNAPSHOTS_DATASET,
) -> ExportResult:
    """Exporta las observaciones de `[start, end)` a Parquet particionado por fecha."""
    with connection.cursor() as cursor:
        cursor.execute(_query(), {"start": start, "end": end})
        rows = cursor.fetchall()
    connection.commit()

    if not rows:
        log.info("nada que exportar", extra={"start": str(start), "end": str(end)})
        return ExportResult(0, 0, 0, 0, None, [])

    frame = pd.DataFrame(rows)
    observations = int(frame["observation_id"].nunique())
    with_prices = int(frame[frame["has_prices"]]["observation_id"].nunique())

    destination = Path(odds_dir) / dataset
    write_partitioned_parquet(frame, destination, partition_cols=["date"])

    files = sorted(destination.rglob("*.parquet"))
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        data = path.read_bytes()
        digest.update(data)
        total_bytes += len(data)

    result = ExportResult(
        rows=len(frame),
        observations=observations,
        with_prices=with_prices,
        without_prices=observations - with_prices,
        destination=destination,
        files=files,
        sha256=digest.hexdigest(),
        bytes_written=total_bytes,
    )
    log.info("exportacion completada", extra=result.as_dict())
    return result


def verify_export(
    connection: psycopg.Connection,
    odds_dir: Path,
    *,
    start: date,
    end: date,
    dataset: str = SNAPSHOTS_DATASET,
) -> dict[str, Any]:
    """Comprueba que el Parquet contiene todas las observaciones del rango.

    Es la comprobacion que debe pasar **antes** de que se pueda purgar nada de
    PostgreSQL.
    """
    from edgecourt.storage import read_parquet

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) AS observaciones
            FROM market_observation
            WHERE observed_at >= %(start)s AND observed_at < %(end)s
            """,
            {"start": start, "end": end},
        )
        in_db = cursor.fetchone()["observaciones"]
    connection.commit()

    source = Path(odds_dir) / dataset
    if not source.exists():
        return {"ok": in_db == 0, "observations_in_db": in_db, "observations_in_file": 0}

    frame = read_parquet(source)
    if frame.empty:
        return {"ok": in_db == 0, "observations_in_db": in_db, "observations_in_file": 0}

    mask = (pd.to_datetime(frame["observed_at"], utc=True).dt.date >= start) & (
        pd.to_datetime(frame["observed_at"], utc=True).dt.date < end
    )
    in_file = int(frame[mask]["observation_id"].nunique())

    return {
        "ok": in_file == in_db,
        "observations_in_db": in_db,
        "observations_in_file": in_file,
    }

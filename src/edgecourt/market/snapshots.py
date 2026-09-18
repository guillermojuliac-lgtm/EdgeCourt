"""Esquema y persistencia de los snapshots de cuotas.

Principio rector (brief §11): **se registra el timestamp real de observacion**,
nunca el teorico. El collector apunta a capturar a 24h, 12h, 6h, 1h, 10m y
cierre, pero un proceso 24/7 sufre caidas, reinicios y mercados que se crean
tarde. Guardar la hora planificada como si fuera la real convertiria esos huecos
en datos falsos.

Por eso cada fila lleva tres cosas distintas:

* `observed_at`      - cuando se leyo de verdad;
* `snapshot_label`   - a que hito apuntaba esa lectura;
* `minutes_to_start` - la distancia real al inicio del partido, calculada.

El analisis posterior debe usar `minutes_to_start`, no la etiqueta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from edgecourt.logging_setup import get_logger
from edgecourt.storage import read_parquet, write_partitioned_parquet

log = get_logger("collector.snapshots")

SNAPSHOTS_DATASET: Final[str] = "betfair_match_odds"

# Hitos objetivo, en minutos antes del inicio. `close` es el ultimo posible.
SNAPSHOT_TARGETS: Final[dict[str, float]] = {
    "24h": 24 * 60,
    "12h": 12 * 60,
    "6h": 6 * 60,
    "1h": 60,
    "10m": 10,
    "close": 2,
}

# Tolerancia de captura por hito, en minutos. Mas estrecha cuanto mas cerca del
# inicio, porque ahi el precio se mueve mas deprisa y un desfase importa mas.
SNAPSHOT_TOLERANCE: Final[dict[str, float]] = {
    "24h": 45.0,
    "12h": 30.0,
    "6h": 20.0,
    "1h": 8.0,
    "10m": 3.0,
    "close": 2.0,
}

SNAPSHOT_COLUMNS: Final[tuple[str, ...]] = (
    # Identidad y tiempo
    "snapshot_id",
    "observed_at",
    "date",
    "snapshot_label",
    "minutes_to_start",
    # Mercado
    "market_id",
    "market_name",
    "market_start_time",
    "market_status",
    "inplay",
    "bet_delay",
    "number_of_active_runners",
    "total_matched",
    "market_total_available",
    # Evento
    "event_id",
    "event_name",
    "competition_name",
    "country_code",
    "timezone",
    # Seleccion
    "selection_id",
    "runner_name",
    "sort_priority",
    "runner_status",
    "runner_total_matched",
    "last_price_traded",
    # Precios: hasta tres niveles de profundidad por lado
    "back_price_1",
    "back_size_1",
    "back_price_2",
    "back_size_2",
    "back_price_3",
    "back_size_3",
    "lay_price_1",
    "lay_size_1",
    "lay_price_2",
    "lay_size_2",
    "lay_price_3",
    "lay_size_3",
)


@dataclass(frozen=True, slots=True)
class PlannedSnapshot:
    """Una captura pendiente: que mercado y a que hito corresponde."""

    market_id: str
    label: str
    market_start_time: datetime


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = pd.to_datetime(value, utc=True, format="ISO8601")
    except (ValueError, TypeError):
        try:
            parsed = pd.to_datetime(value, utc=True)
        except (ValueError, TypeError):
            return None
    return parsed.to_pydatetime() if not pd.isna(parsed) else None


def snapshot_id(market_id: str, selection_id: Any, label: str) -> str:
    """Identificador estable de una observacion.

    Un hito por mercado y seleccion: si el collector reintenta tras un fallo
    parcial, la segunda captura sustituye a la primera en lugar de duplicarla.
    """
    return f"{market_id}:{selection_id}:{label}"


def _price_levels(entries: list[dict[str, Any]] | None, depth: int = 3) -> list[tuple[Any, Any]]:
    levels: list[tuple[Any, Any]] = []
    for entry in (entries or [])[:depth]:
        levels.append((entry.get("price"), entry.get("size")))
    while len(levels) < depth:
        levels.append((None, None))
    return levels


def normalise_market_book(
    book: dict[str, Any],
    catalogue: dict[str, Any],
    *,
    label: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """Convierte la respuesta de `listMarketBook` en filas del esquema canonico.

    `catalogue` aporta los nombres (evento, competicion, jugadores) que
    `listMarketBook` no devuelve.
    """
    market_id = book.get("marketId") or catalogue.get("marketId")
    if not market_id:
        return []

    start_time = _parse_time(catalogue.get("marketStartTime"))
    minutes_to_start = (
        (start_time - observed_at).total_seconds() / 60.0 if start_time is not None else None
    )

    event = catalogue.get("event") or {}
    competition = catalogue.get("competition") or {}
    runner_names = {
        runner.get("selectionId"): runner.get("runnerName")
        for runner in catalogue.get("runners", [])
    }
    sort_priorities = {
        runner.get("selectionId"): runner.get("sortPriority")
        for runner in catalogue.get("runners", [])
    }

    market_available = 0.0
    rows: list[dict[str, Any]] = []

    for runner in book.get("runners", []):
        selection_id = runner.get("selectionId")
        ex = runner.get("ex") or {}
        backs = _price_levels(ex.get("availableToBack"))
        lays = _price_levels(ex.get("availableToLay"))
        market_available += sum(size or 0.0 for _, size in backs + lays)

        row: dict[str, Any] = {
            "snapshot_id": snapshot_id(market_id, selection_id, label),
            "observed_at": observed_at,
            "date": observed_at.date().isoformat(),
            "snapshot_label": label,
            "minutes_to_start": minutes_to_start,
            "market_id": market_id,
            "market_name": catalogue.get("marketName"),
            "market_start_time": start_time,
            "market_status": book.get("status"),
            "inplay": book.get("inplay"),
            "bet_delay": book.get("betDelay"),
            "number_of_active_runners": book.get("numberOfActiveRunners"),
            "total_matched": book.get("totalMatched"),
            "market_total_available": None,  # se completa al final
            "event_id": event.get("id"),
            "event_name": event.get("name"),
            "competition_name": competition.get("name"),
            "country_code": event.get("countryCode"),
            "timezone": event.get("timezone"),
            "selection_id": selection_id,
            "runner_name": runner_names.get(selection_id),
            "sort_priority": sort_priorities.get(selection_id),
            "runner_status": runner.get("status"),
            "runner_total_matched": runner.get("totalMatched"),
            "last_price_traded": runner.get("lastPriceTraded"),
        }
        for index, (price, size) in enumerate(backs, start=1):
            row[f"back_price_{index}"] = price
            row[f"back_size_{index}"] = size
        for index, (price, size) in enumerate(lays, start=1):
            row[f"lay_price_{index}"] = price
            row[f"lay_size_{index}"] = size
        rows.append(row)

    for row in rows:
        row["market_total_available"] = market_available

    return rows


def to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Construye el DataFrame canonico, con todas las columnas del esquema."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=list(SNAPSHOT_COLUMNS))
    for column in SNAPSHOT_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[list(SNAPSHOT_COLUMNS)]


def existing_snapshot_ids(odds_dir: Path, *, dates: list[str] | None = None) -> set[str]:
    """Identificadores ya almacenados, para no repetir capturas.

    Solo se leen las particiones de las fechas indicadas: cargar el historico
    entero para deduplicar seria innecesario y crecería sin limite.
    """
    dataset = odds_dir / SNAPSHOTS_DATASET
    if not dataset.exists():
        return set()

    sources: list[Path] = []
    if dates:
        for value in dates:
            partition = dataset / f"date={value}"
            if partition.exists():
                sources.extend(partition.rglob("*.parquet"))
    else:
        sources.extend(dataset.rglob("*.parquet"))

    if not sources:
        return set()

    import pyarrow.dataset as ds

    table = ds.dataset(sources, format="parquet").to_table(columns=["snapshot_id"])
    return set(table.column("snapshot_id").to_pylist())


def append_snapshots(odds_dir: Path, frame: pd.DataFrame) -> int:
    """Anade snapshots al dataset, deduplicando por `snapshot_id`.

    Las particiones tocadas se reescriben completas conservando lo que ya habia:
    una captura repetida del mismo hito sustituye a la anterior en lugar de
    duplicarla.
    """
    if frame.empty:
        return 0

    dataset = odds_dir / SNAPSHOTS_DATASET
    frame = frame.drop_duplicates(subset=["snapshot_id"], keep="last")

    written = 0
    for date_value, partition in frame.groupby("date", dropna=True):
        target = dataset / f"date={date_value}"
        if target.exists():
            existing = read_parquet(target)
            existing = existing[~existing["snapshot_id"].isin(set(partition["snapshot_id"]))]
            partition = pd.concat([existing, partition], ignore_index=True)
        partition = partition.copy()
        partition["date"] = date_value
        write_partitioned_parquet(partition, dataset, partition_cols=["date"])
        written += len(partition)

    log.info("snapshots almacenados", extra={"rows": len(frame), "partitions_rows": written})
    return len(frame)


def due_snapshots(
    catalogues: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    already_captured: set[str] | None = None,
) -> list[PlannedSnapshot]:
    """Decide que capturas tocan ahora mismo.

    Un hito esta "vencido" si el tiempo restante hasta el inicio esta dentro de
    su ventana de tolerancia y todavia no se ha capturado. Los hitos que ya
    pasaron sin capturarse **no se recuperan**: un snapshot de 24h tomado a 3
    horas del inicio no es un snapshot de 24h, seria un dato falso.
    """
    now = now or datetime.now(UTC)
    already_captured = already_captured or set()
    planned: list[PlannedSnapshot] = []

    for catalogue in catalogues:
        market_id = catalogue.get("marketId")
        start_time = _parse_time(catalogue.get("marketStartTime"))
        if not market_id or start_time is None:
            continue

        minutes_left = (start_time - now).total_seconds() / 60.0
        if minutes_left < 0:
            continue

        for label, target in SNAPSHOT_TARGETS.items():
            tolerance = SNAPSHOT_TOLERANCE[label]
            if not (target - tolerance <= minutes_left <= target + tolerance):
                continue
            if any(
                sid.endswith(f":{label}") and sid.startswith(f"{market_id}:")
                for sid in already_captured
            ):
                continue
            planned.append(
                PlannedSnapshot(market_id=market_id, label=label, market_start_time=start_time)
            )

    return planned


def coverage_report(odds_dir: Path) -> pd.DataFrame:
    """Cuantos snapshots hay por hito. Mide los huecos, que siempre existiran."""
    dataset = odds_dir / SNAPSHOTS_DATASET
    if not dataset.exists():
        return pd.DataFrame(columns=["snapshot_label", "markets", "rows"])

    frame = read_parquet(dataset)
    if frame.empty:
        return pd.DataFrame(columns=["snapshot_label", "markets", "rows"])

    grouped = (
        frame.groupby("snapshot_label")
        .agg(markets=("market_id", "nunique"), rows=("snapshot_id", "count"))
        .reset_index()
    )
    order = {label: i for i, label in enumerate(SNAPSHOT_TARGETS)}
    grouped["_order"] = grouped["snapshot_label"].map(order).fillna(99)
    return grouped.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)

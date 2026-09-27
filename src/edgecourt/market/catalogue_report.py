"""Informe de la auditoria de catalogo (Phase 3.5-C2).

Lee los artefactos de `catalogue_audit` y produce CSV y un resumen para el
documento de investigacion. Es el UNICO componente de 3.5-C2 que puede tocar
PostgreSQL, y solo en modo lectura, para cruzar los mercados auditados con
`betfair_market` (la pregunta "los descubre tambien el collector normal?").

Las metricas de libro reutilizan las definiciones del collector
(`observation_from_book`: `total_available`, `max_spread_pct`) y anaden las de la
auditoria de la Semana 1 (`top_depth`, spread del favorito, spread en ticks),
para que las cifras sean comparables. El volumen casado solo existe por mercado:
la Delayed Key no lo da por selección y aqui no se inventa.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from edgecourt.market.catalogue_audit import (
    EXPERIMENT_END,
    EXPERIMENT_START,
    SCHEMA_VERSION,
    SLOT_MINUTES,
    STATUS_OK,
    AuditPaths,
    iso_utc,
    load_run,
    slot_for,
    to_utc,
)

# --- Escala de precios de Betfair ---------------------------------------------

_LADDER_BANDS = (
    (1.01, 2.0, 0.01),
    (2.0, 3.0, 0.02),
    (3.0, 4.0, 0.05),
    (4.0, 6.0, 0.1),
    (6.0, 10.0, 0.2),
    (10.0, 20.0, 0.5),
    (20.0, 30.0, 1.0),
    (30.0, 50.0, 2.0),
    (50.0, 100.0, 5.0),
    (100.0, 1000.0, 10.0),
)


def _build_ladder() -> list[float]:
    ladder: list[float] = []
    for low, high, step in _LADDER_BANDS:
        steps = round((high - low) / step)
        ladder.extend(round(low + i * step, 2) for i in range(steps))
    ladder.append(1000.0)
    return ladder


LADDER = _build_ladder()


def tick_index(price: float) -> int:
    """Posicion en la escala oficial (el tick mas cercano)."""
    return min(range(len(LADDER)), key=lambda i: abs(LADDER[i] - price))


def spread_ticks(back: float, lay: float) -> int:
    return tick_index(lay) - tick_index(back)


# --- Lectura de artefactos ----------------------------------------------------


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def load_runs(root: Path, since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Todos los `run.json` validos del intervalo, ordenados por slot.

    Se ignoran temporales (`.tmp-*`), JSON ilegible y esquemas desconocidos.
    """
    runs = []
    for path in sorted(root.glob("????-??-??/????_run.json")):
        run = load_run(path)
        if not run or run.get("schema_version") != SCHEMA_VERSION:
            continue
        slot = _parse_utc(run.get("scheduled_slot_utc"))
        if slot is None or not (since <= slot < until):
            continue
        runs.append(run)
    return sorted(runs, key=lambda r: r["scheduled_slot_utc"])


def load_catalogue(root: Path, digest: str) -> list[dict[str, Any]]:
    raw = gzip.decompress(AuditPaths(root).catalogue_path(digest).read_bytes())
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"catalogo {digest} corrupto: el hash no coincide")
    return json.loads(raw)


def load_books(root: Path, run: dict[str, Any]) -> list[dict[str, Any]]:
    slot = _parse_utc(run["scheduled_slot_utc"])
    raw = gzip.decompress(AuditPaths(root).books_path(slot).read_bytes())
    if run.get("books_sha256") and hashlib.sha256(raw).hexdigest() != run["books_sha256"]:
        raise ValueError(f"libros de {run['run_id']} corruptos: el hash no coincide")
    doc = json.loads(raw)
    if doc.get("run_id") != run["run_id"]:
        raise ValueError(f"libros de {run['run_id']} pertenecen a otro run")
    return doc.get("books") or []


def expected_slots(since: datetime, until: datetime) -> list[datetime]:
    slots, current = [], slot_for(since)
    if current < since:
        current += timedelta(minutes=SLOT_MINUTES)
    while current < until:
        slots.append(current)
        current += timedelta(minutes=SLOT_MINUTES)
    return slots


# --- Metricas de libro --------------------------------------------------------


def book_metrics(
    book: dict[str, Any], catalogue_market: dict[str, Any], observed_at: datetime
) -> dict[str, Any]:
    """Metricas de un libro con las definiciones del collector y de la Semana 1."""
    from edgecourt.market.persistence import observation_from_book

    payload = observation_from_book(
        book,
        catalogue_market,
        label="catalogue_audit",
        capture_key="catalogue_audit",
        observed_at=observed_at,
        collector_run_id=uuid.UUID(int=0),
    )
    two_sided = [
        r
        for r in payload.runners
        if r.get("back_price_1") is not None and r.get("lay_price_1") is not None
    ]
    top_depth = min(
        (min(float(r["back_size_1"] or 0), float(r["lay_size_1"] or 0)) for r in two_sided),
        default=None,
    )
    ticks = [spread_ticks(float(r["back_price_1"]), float(r["lay_price_1"])) for r in two_sided]
    favourite = min(two_sided, key=lambda r: float(r["back_price_1"]), default=None)
    fav_spread = None
    if favourite is not None:
        back, lay = float(favourite["back_price_1"]), float(favourite["lay_price_1"])
        fav_spread = (lay - back) / back * 100.0
    return {
        "market_status": payload.market_status,
        "inplay": payload.inplay,
        "market_data_delayed": book.get("isMarketDataDelayed"),
        "minutes_to_start": round(payload.minutes_to_start, 2),
        "has_prices": payload.has_prices,
        "has_back_and_lay": bool(two_sided),
        "total_available": round(payload.total_available, 2),
        "top_depth": None if top_depth is None else round(top_depth, 2),
        "max_spread_pct": None
        if payload.max_spread_pct is None
        else round(payload.max_spread_pct, 4),
        "fav_spread_pct": None if fav_spread is None else round(fav_spread, 4),
        "max_spread_ticks": max(ticks) if ticks else None,
        # Solo por mercado: la Delayed Key no da el volumen por seleccion.
        "total_matched_market": book.get("totalMatched"),
    }


# --- Informe ------------------------------------------------------------------


@dataclass
class Report:
    since: datetime
    until: datetime
    runs: list[dict[str, Any]] = field(default_factory=list)
    markets: dict[str, dict[str, Any]] = field(default_factory=dict)
    books: list[dict[str, Any]] = field(default_factory=list)
    expected: int = 0
    ok_runs: int = 0
    error_runs: int = 0

    @property
    def coverage_pct(self) -> float:
        return round(100.0 * self.ok_runs / self.expected, 2) if self.expected else 0.0


def build_report(
    root: Path,
    *,
    since: datetime = EXPERIMENT_START,
    until: datetime = EXPERIMENT_END,
    now: datetime | None = None,
    collector_lookup: Callable[[list[str]], dict[str, Any]] | None = None,
) -> Report:
    """Agrega los artefactos. `collector_lookup` hace el cruce con PostgreSQL."""
    since, until = to_utc(since), to_utc(until)
    # El slot en curso no cuenta como esperado: puede no haberse ejecutado aun
    # (el timer anade hasta 2 minutos de retraso aleatorio).
    horizon = min(until, slot_for(to_utc(now or datetime.now(UTC))))
    report = Report(since=since, until=until)
    report.expected = len(expected_slots(since, horizon))
    report.runs = load_runs(root, since, until)

    for run in report.runs:
        if run.get("status") != STATUS_OK:
            report.error_runs += 1
            continue
        report.ok_runs += 1
        slot = _parse_utc(run["scheduled_slot_utc"])
        captured = _parse_utc(run.get("captured_at_utc")) or slot
        catalogue = {m["marketId"]: m for m in load_catalogue(root, run["catalogue_hash"])}
        match_odds = set(run.get("match_odds_market_ids") or [])

        for market_id, market in catalogue.items():
            entry = report.markets.setdefault(
                market_id,
                {
                    "market_id": market_id,
                    "competition": (market.get("competition") or {}).get("name"),
                    "competition_id": (market.get("competition") or {}).get("id"),
                    "event": (market.get("event") or {}).get("name"),
                    "country_code": (market.get("event") or {}).get("countryCode"),
                    "is_match_odds": market_id in match_odds,
                    "first_seen_slot": iso_utc(slot),
                    "first_seen_captured_at": iso_utc(captured),
                    "runs_present": 0,
                },
            )
            entry["last_seen_slot"] = iso_utc(slot)
            entry["market_start_time_last"] = market.get("marketStartTime")
            entry["runs_present"] += 1

        for book in load_books(root, run):
            market = catalogue.get(book.get("marketId"), {})
            row = {
                "run_id": run["run_id"],
                "slot": iso_utc(slot),
                "market_id": book.get("marketId"),
            }
            row.update(book_metrics(book, market, captured))
            report.books.append(row)

    for entry in report.markets.values():
        start = _parse_utc(entry.get("market_start_time_last"))
        first = _parse_utc(entry["first_seen_captured_at"])
        entry["lead_time_hours"] = (
            round((start - first).total_seconds() / 3600, 2) if start and first else None
        )

    if collector_lookup is not None and report.markets:
        seen = collector_lookup(sorted(report.markets))
        for market_id, entry in report.markets.items():
            entry["in_collector"] = market_id in seen
    return report


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


RUN_COLUMNS = [
    "run_id",
    "scheduled_slot_utc",
    "captured_at_utc",
    "status",
    "duration_s",
    "catalogue_hash",
    "tennis_market_count",
    "match_odds_count",
    "competition_count",
    "book_market_count",
    "market_data_delayed",
]
MARKET_COLUMNS = [
    "market_id",
    "competition",
    "competition_id",
    "event",
    "country_code",
    "is_match_odds",
    "market_start_time_last",
    "first_seen_slot",
    "last_seen_slot",
    "runs_present",
    "lead_time_hours",
    "in_collector",
]
BOOK_COLUMNS = [
    "run_id",
    "slot",
    "market_id",
    "market_status",
    "inplay",
    "market_data_delayed",
    "minutes_to_start",
    "has_prices",
    "has_back_and_lay",
    "total_available",
    "top_depth",
    "max_spread_pct",
    "fav_spread_pct",
    "max_spread_ticks",
    "total_matched_market",
]


def write_report(report: Report, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "runs": out_dir / "runs.csv",
        "markets": out_dir / "markets.csv",
        "books": out_dir / "books.csv",
        "summary": out_dir / "summary.json",
    }
    _write_csv(files["runs"], report.runs, RUN_COLUMNS)
    _write_csv(files["markets"], report.markets.values(), MARKET_COLUMNS)
    _write_csv(files["books"], report.books, BOOK_COLUMNS)
    competitions: dict[str, int] = {}
    for entry in report.markets.values():
        name = entry.get("competition") or "(sin competicion)"
        competitions[name] = competitions.get(name, 0) + 1
    summary = {
        "since": iso_utc(report.since),
        "until": iso_utc(report.until),
        "expected_slots": report.expected,
        "ok_runs": report.ok_runs,
        "error_runs": report.error_runs,
        "coverage_pct": report.coverage_pct,
        "markets": len(report.markets),
        "match_odds_markets": sum(1 for m in report.markets.values() if m["is_match_odds"]),
        "competitions": dict(sorted(competitions.items())),
        "book_rows": len(report.books),
    }
    files["summary"].write_text(json.dumps(summary, indent=1, ensure_ascii=False), "utf-8")
    return files


# --- Cruce con PostgreSQL (solo lectura) --------------------------------------


def collector_lookup_readonly(dsn: str, connect: Callable[..., Any] | None = None):
    """Devuelve una funcion que consulta `betfair_market` en una sesion READ ONLY."""
    if connect is None:
        from edgecourt.db.connection import connect as db_connect

        connect = db_connect

    def lookup(market_ids: list[str]) -> dict[str, Any]:
        with connect(dsn) as connection:
            # Antes de cualquier consulta: toda la sesion es de solo lectura.
            connection.read_only = True
            cursor = connection.execute(
                "SELECT market_id, first_seen_at FROM betfair_market WHERE market_id = ANY(%s)",
                (market_ids,),
            )
            rows = cursor.fetchall()
            connection.rollback()
        return {row["market_id"]: row["first_seen_at"] for row in rows}

    return lookup

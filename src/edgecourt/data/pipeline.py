"""Orquestacion de la ingesta: descarga -> normalizacion -> validacion -> Parquet."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from edgecourt.config import Settings
from edgecourt.data import ingest, sources, validation
from edgecourt.logging_setup import get_logger
from edgecourt.storage import read_parquet, write_partitioned_parquet

log = get_logger("data.pipeline")

MATCHES_DATASET = "matches"


@dataclass(slots=True)
class IngestResult:
    rows: int
    removed_duplicates: int
    report: validation.ValidationReport
    summary: dict[str, object]
    coverage: pd.DataFrame
    destination: Path


def fetch(
    settings: Settings,
    *,
    years: range,
    include_reference: bool = True,
    force: bool = False,
) -> dict[str, int]:
    """Descarga los CSV crudos. Accion explicita, nunca implicita."""
    primary = sources.fetch_tennismylife(settings.raw_dir, years, force=force)
    counts = {"tennismylife": len(primary)}
    if include_reference:
        reference = sources.fetch_sackmann_mirror(settings.raw_dir, years, force=force)
        counts["sackmann_mirror"] = len(reference)
    return counts


def build_match_facts(
    settings: Settings, *, min_year: int, max_year: int | None = None
) -> IngestResult:
    """Construye el dataset canonico a partir de los CSV descargados."""
    df = ingest.ingest_directory(
        settings.raw_dir / "tml",
        source="tennismylife",
        min_year=min_year,
        max_year=max_year,
    )
    df, removed = ingest.deduplicate(df)

    report = validation.validate_match_facts(df)
    summary = ingest.summarise(df)
    coverage = validation.year_coverage(df)

    destination = settings.processed_dir / MATCHES_DATASET
    write_partitioned_parquet(df, destination, partition_cols=["year"])

    _write_report(
        settings,
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "tennismylife",
            "min_year": min_year,
            "max_year": max_year,
            "summary": summary,
            "removed_duplicates": removed,
            "errors": [str(i) for i in report.errors],
            "warnings": [str(i) for i in report.warnings],
            "coverage": coverage.to_dict(orient="records"),
        },
        name="ingest_report.json",
    )

    return IngestResult(
        rows=len(df),
        removed_duplicates=removed,
        report=report,
        summary=summary,
        coverage=coverage,
        destination=destination,
    )


def cross_check_against_reference(
    settings: Settings, *, min_year: int, max_year: int | None = None
) -> dict[str, object]:
    """Contrasta el dataset canonico con el mirror de referencia."""
    primary = read_parquet(settings.processed_dir / MATCHES_DATASET)
    reference = ingest.ingest_directory(
        settings.raw_dir / "sackmann_mirror",
        source="sackmann_mirror",
        min_year=min_year,
        max_year=max_year,
    )
    result = validation.cross_check(primary, reference)
    _write_report(
        settings, result | {"generated_at": datetime.now(UTC).isoformat()}, "cross_check.json"
    )
    return result


def _write_report(settings: Settings, payload: dict, name: str) -> Path:
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    path = settings.results_dir / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("informe escrito", extra={"path": str(path)})
    return path

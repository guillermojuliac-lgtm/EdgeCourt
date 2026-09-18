"""Test de extremo a extremo de la ingesta: CSV en disco -> Parquet + informes."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from tests.factories import _raw_frame

from edgecourt.data import pipeline
from edgecourt.storage import read_parquet


@pytest.fixture
def settings_with_raw_csv(settings_factory):
    """Prepara un `data/raw/tml` con dos anos de CSV sinteticos."""
    settings = settings_factory()
    settings.ensure_directories()
    tml = settings.raw_dir / "tml"
    tml.mkdir(parents=True, exist_ok=True)

    for year in (2022, 2023):
        frame = _raw_frame(60)
        frame["tourney_id"] = f"{year}-560"
        frame["tourney_date"] = f"{year}0417"
        frame.to_csv(tml / f"{year}.csv", index=False)
    return settings


def test_build_match_facts_writes_partitioned_parquet(settings_with_raw_csv):
    settings = settings_with_raw_csv
    result = pipeline.build_match_facts(settings, min_year=2000)

    assert result.rows == 120
    assert (result.destination / "year=2022").is_dir()
    assert (result.destination / "year=2023").is_dir()

    stored = read_parquet(result.destination)
    assert len(stored) == 120
    assert set(stored["year"].astype(int)) == {2022, 2023}


def test_build_match_facts_writes_an_ingest_report(settings_with_raw_csv):
    settings = settings_with_raw_csv
    pipeline.build_match_facts(settings, min_year=2000)

    report = json.loads((settings.results_dir / "ingest_report.json").read_text())
    assert report["source"] == "tennismylife"
    assert report["summary"]["rows"] == 120
    assert "coverage" in report
    assert report["summary"]["target_mean"] == pytest.approx(0.5, abs=0.2)


def test_year_filter_is_respected(settings_with_raw_csv):
    result = pipeline.build_match_facts(settings_with_raw_csv, min_year=2023)
    assert result.rows == 60
    assert result.summary["year_min"] == 2023


def test_missing_raw_directory_fails_clearly(settings_factory):
    settings = settings_factory()
    settings.ensure_directories()
    with pytest.raises(FileNotFoundError, match="data fetch"):
        pipeline.build_match_facts(settings, min_year=2000)


def test_cross_check_writes_report(settings_with_raw_csv):
    settings = settings_with_raw_csv
    pipeline.build_match_facts(settings, min_year=2000)

    mirror = settings.raw_dir / "sackmann_mirror"
    mirror.mkdir(parents=True, exist_ok=True)
    for year in (2022, 2023):
        frame = _raw_frame(60)
        frame["tourney_id"] = f"{year}-560"
        frame["tourney_date"] = f"{year}0417"
        frame.drop(columns=["indoor"]).to_csv(mirror / f"atp_matches_{year}.csv", index=False)

    result = pipeline.cross_check_against_reference(settings, min_year=2000)

    assert result["matched"] == 120
    assert result["winner_agreement_pct"] == 100.0
    assert (settings.results_dir / "cross_check.json").exists()


def test_parquet_roundtrip_preserves_the_schema(settings_with_raw_csv):
    """Los tipos deben sobrevivir al viaje por Parquet."""
    from edgecourt.data import schema

    result = pipeline.build_match_facts(settings_with_raw_csv, min_year=2000)
    stored = read_parquet(result.destination)

    for column in schema.MATCH_FACTS_COLUMNS:
        assert column in stored.columns, column
    assert pd.api.types.is_datetime64_any_dtype(stored["date"])
    assert stored["match_id"].is_unique

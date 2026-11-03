"""`edgecourt data freshness`: antiguedad del dataset de partidos (solo lectura)."""

from __future__ import annotations

import argparse

import pandas as pd

from edgecourt import cli


def _write_matches(settings, last_date: str) -> None:
    folder = settings.processed_dir / "matches"
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": pd.to_datetime(["2026-01-05", last_date])}).to_parquet(
        folder / "part.parquet"
    )


def test_fresh_dataset_exits_zero(settings_factory, capsys):
    settings = settings_factory()
    _write_matches(settings, pd.Timestamp.now().strftime("%Y-%m-%d"))
    code = cli._cmd_data_freshness(settings, argparse.Namespace(max_days=14))
    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_stale_dataset_exits_one_and_says_how_to_refresh(settings_factory, capsys):
    settings = settings_factory()
    _write_matches(settings, "2026-01-10")
    code = cli._cmd_data_freshness(settings, argparse.Namespace(max_days=14))
    out = capsys.readouterr().out
    assert code == 1
    assert "OBSOLETO" in out and "data fetch" in out

"""Tests de la CLI."""

from __future__ import annotations

import pytest

from edgecourt.cli import PENDING, build_parser, main


def test_status_runs_and_reports_paper_mode(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    from edgecourt.config import get_settings

    get_settings.cache_clear()

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "PAPER" in out
    assert "NO IMPLEMENTADAS" in out
    get_settings.cache_clear()


@pytest.mark.parametrize("key", sorted(PENDING))
def test_pending_commands_are_declared_and_exit_nonzero(key, capsys):
    assert main(key.split()) == 3
    out = capsys.readouterr().out
    assert PENDING[key][0] in out


def test_unknown_command_fails():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["inventado"])


def test_no_command_fails():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


# --- Comandos de datos -------------------------------------------------------


@pytest.fixture
def cli_settings(monkeypatch, tmp_path):
    """Aisla la CLI en un directorio temporal."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    from edgecourt.config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_data_fetch_announces_sources_before_downloading(cli_settings, capsys, monkeypatch):
    """La descarga debe declarar que fuentes usa y bajo que condiciones."""
    called = {}

    def fake_fetch(settings, *, years, include_reference, force):
        called["years"] = years
        called["include_reference"] = include_reference
        return {"tennismylife": len(list(years))}

    monkeypatch.setattr("edgecourt.cli.pipeline.fetch", fake_fetch)

    assert main(["data", "fetch", "--from-year", "2020", "--to-year", "2022"]) == 0
    out = capsys.readouterr().out
    assert "TennisMyLife" in out
    assert "no comercial" in out
    assert called["years"] == range(2020, 2023)


def test_data_fetch_can_skip_the_reference_source(cli_settings, monkeypatch):
    captured = {}

    def fake_fetch(settings, *, years, include_reference, force):
        captured["include_reference"] = include_reference
        return {}

    monkeypatch.setattr("edgecourt.cli.pipeline.fetch", fake_fetch)
    main(["data", "fetch", "--no-reference", "--from-year", "2020", "--to-year", "2020"])
    assert captured["include_reference"] is False


def test_data_import_reports_target_balance(cli_settings, capsys, monkeypatch):
    """La CLI debe mostrar P(target=1): es el chivato de la aleatorizacion A/B."""
    import pandas as pd

    from edgecourt.data import pipeline as real_pipeline
    from edgecourt.data.validation import ValidationReport

    fake = real_pipeline.IngestResult(
        rows=1000,
        removed_duplicates=2,
        report=ValidationReport(rows=1000),
        summary={
            "rows": 1000,
            "year_min": 2000,
            "year_max": 2024,
            "target_mean": 0.501,
            "pct_with_match_stats": 91.1,
            "pct_with_rank": 97.4,
            "pct_with_indoor": 91.5,
            "surface_counts": {"hard": 600},
            "completion_counts": {"completed": 980},
        },
        coverage=pd.DataFrame({"year": [2000], "matches": [1000]}),
        destination=cli_settings.processed_dir / "matches",
    )
    monkeypatch.setattr("edgecourt.cli.pipeline.build_match_facts", lambda *a, **k: fake)

    assert main(["data", "import"]) == 0
    out = capsys.readouterr().out
    assert "0.5010" in out
    assert "sin incidencias" in out


def test_data_import_exits_nonzero_when_validation_finds_errors(cli_settings, monkeypatch):
    """Un dataset con errores no puede pasar como bueno."""
    import pandas as pd

    from edgecourt.data import pipeline as real_pipeline
    from edgecourt.data.validation import Issue, ValidationReport

    report = ValidationReport(rows=10, issues=[Issue("ERROR", "x", "roto", 3)])
    fake = real_pipeline.IngestResult(
        rows=10,
        removed_duplicates=0,
        report=report,
        summary={
            "rows": 10,
            "year_min": 2000,
            "year_max": 2000,
            "target_mean": 0.5,
            "pct_with_match_stats": 0.0,
            "pct_with_rank": 0.0,
            "pct_with_indoor": 0.0,
            "surface_counts": {},
            "completion_counts": {},
        },
        coverage=pd.DataFrame(),
        destination=cli_settings.processed_dir / "matches",
    )
    monkeypatch.setattr("edgecourt.cli.pipeline.build_match_facts", lambda *a, **k: fake)

    assert main(["data", "import"]) == 4


def test_data_check_reports_winner_agreement(cli_settings, capsys, monkeypatch):
    monkeypatch.setattr(
        "edgecourt.cli.pipeline.cross_check_against_reference",
        lambda *a, **k: {
            "overlap_years": [2023, 2024],
            "primary_rows": 100,
            "reference_rows": 98,
            "matched": 95,
            "match_rate_primary": 95.0,
            "match_rate_primary_compact": 97.0,
            "match_rate_reference": 96.9,
            "primary_only": 5,
            "reference_only": 3,
            "winner_agreement_pct": 100.0,
        },
    )
    assert main(["data", "check"]) == 0
    assert "acuerdo en ganador   : 100.0%" in capsys.readouterr().out


def test_data_check_without_overlap_exits_nonzero(cli_settings, monkeypatch):
    monkeypatch.setattr(
        "edgecourt.cli.pipeline.cross_check_against_reference",
        lambda *a, **k: {"overlap_years": []},
    )
    assert main(["data", "check"]) == 4

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

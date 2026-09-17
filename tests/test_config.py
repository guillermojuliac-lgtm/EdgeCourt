"""Tests de configuracion.

El test critico es `test_live_betting_mode_is_rejected`: es la barrera que
impide que exista una ruta accidental a apuestas reales (brief §4 y §28).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from edgecourt.config import PROJECT_ROOT, REAL_BETTING_ENABLED, Settings


@pytest.mark.critical
@pytest.mark.parametrize("mode", ["live", "real", "LIVE", "Paper ", "production", ""])
def test_live_betting_mode_is_rejected(settings_factory, mode):
    """Cualquier modo distinto de 'paper' impide arrancar."""
    with pytest.raises(ValidationError):
        settings_factory(betting_mode=mode)


@pytest.mark.critical
def test_paper_is_the_only_accepted_mode(settings_factory):
    assert settings_factory(betting_mode="paper").betting_mode == "paper"
    assert settings_factory().betting_mode == "paper"


@pytest.mark.critical
def test_real_betting_flag_is_false():
    assert REAL_BETTING_ENABLED is False


@pytest.mark.critical
def test_full_kelly_is_rejected(settings_factory):
    """Full Kelly (1.0) y cualquier fraccion > 0.25 quedan prohibidos (brief §14)."""
    for fraction in (1.0, 0.5, 0.26):
        with pytest.raises(ValidationError):
            settings_factory(kelly_fraction=fraction)
    assert settings_factory(kelly_fraction=0.25).kelly_fraction == 0.25


def test_relative_paths_resolve_against_project_root(isolated_env):
    settings = Settings(data_dir=Path("data"), _env_file=None)
    assert settings.data_dir == PROJECT_ROOT / "data"
    assert settings.raw_dir == PROJECT_ROOT / "data" / "raw"


def test_absolute_paths_are_preserved(settings_factory, tmp_path):
    settings = settings_factory(data_dir=tmp_path / "otro")
    assert settings.data_dir == tmp_path / "otro"


def test_exposure_limits_must_be_coherent(settings_factory):
    with pytest.raises(ValidationError, match="MAX_MARKET_EXPOSURE"):
        settings_factory(max_market_exposure=0.5, max_daily_exposure=0.1)
    with pytest.raises(ValidationError, match="MAX_STAKE_PERCENTAGE"):
        settings_factory(max_stake_percentage=0.05, max_market_exposure=0.02)


def test_telegram_requires_credentials(settings_factory):
    with pytest.raises(ValidationError, match="TELEGRAM"):
        settings_factory(telegram_enabled=True)
    ok = settings_factory(telegram_enabled=True, telegram_bot_token="t", telegram_chat_id="c")
    assert ok.telegram_enabled


def test_settings_are_immutable(settings_factory):
    settings = settings_factory()
    with pytest.raises(ValidationError):
        settings.bankroll = 99999.0


def test_ensure_directories_is_idempotent(settings_factory):
    settings = settings_factory()
    settings.ensure_directories()
    settings.ensure_directories()
    assert settings.raw_dir.is_dir()
    assert settings.production_dir.is_dir()
    assert settings.challenger_dir.is_dir()

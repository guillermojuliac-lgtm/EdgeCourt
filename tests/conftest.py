"""Fixtures compartidas.

Importante: los tests no deben leer el `.env` real del desarrollador, porque su
contenido cambiaria el resultado. `isolated_env` limpia el entorno relevante y
desactiva la lectura del fichero.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from edgecourt.config import Settings

_ENV_PREFIXES = (
    "ENVIRONMENT",
    "LOG_LEVEL",
    "BETTING_MODE",
    "DATA_DIR",
    "MODELS_DIR",
    "LOGS_DIR",
    "BETFAIR_",
    "BANKROLL",
    "MAX_",
    "MINIMUM_",
    "MAXIMUM_",
    "KELLY_",
    "TELEGRAM_",
)


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def settings_factory(isolated_env, tmp_path: Path):
    """Construye Settings ignorando el .env real del proyecto."""

    def _factory(**overrides) -> Settings:
        defaults = {
            "data_dir": tmp_path / "data",
            "models_dir": tmp_path / "models",
            "logs_dir": tmp_path / "logs",
            "_env_file": None,
        }
        return Settings(**{**defaults, **overrides})

    return _factory

"""Configuracion tipada de EdgeCourt.

Toda la configuracion sensible llega por variables de entorno o por un fichero
`.env` local que nunca se versiona.

Invariante de seguridad de esta version: `BETTING_MODE` solo admite el valor
``"paper"``. Cualquier otro valor impide arrancar la aplicacion. No existe una
variable que habilite apuestas reales porque no existe el codigo que las
enviaria (ver `tests/test_no_real_betting_surface.py`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raiz del proyecto: .../src/edgecourt/config.py -> subir 3 niveles.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Constante explicita y no configurable. Sirve como aserto legible en el codigo
# y como punto unico al que apuntarian los tests si algun dia cambiara.
REAL_BETTING_ENABLED: bool = False


class Settings(BaseSettings):
    """Configuracion de la aplicacion, validada en el arranque."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Entorno -----------------------------------------------------------
    environment: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Modo de apuesta ---------------------------------------------------
    # `Literal["paper"]` es la barrera dura: pydantic rechaza "live".
    betting_mode: Literal["paper"] = "paper"

    # --- Rutas -------------------------------------------------------------
    data_dir: Path = Path("data")
    models_dir: Path = Path("models")
    logs_dir: Path = Path("logs")

    # --- Betfair (solo lectura, PHASE 8) -----------------------------------
    betfair_username: str = ""
    betfair_password: str = ""
    betfair_app_key: str = ""
    betfair_cert_path: Path | None = None
    betfair_key_path: Path | None = None

    # --- Riesgo (paper) ----------------------------------------------------
    bankroll: float = Field(default=1000.0, gt=0)
    max_stake_percentage: float = Field(default=0.01, gt=0, le=0.10)
    max_daily_exposure: float = Field(default=0.05, gt=0, le=1.0)
    max_market_exposure: float = Field(default=0.02, gt=0, le=1.0)
    minimum_edge: float = Field(default=0.03, ge=0.0, lt=1.0)
    minimum_liquidity: float = Field(default=50.0, ge=0.0)
    maximum_drawdown: float = Field(default=0.20, gt=0, le=1.0)
    # Full Kelly (1.0) queda prohibido por el limite superior del campo.
    kelly_fraction: float = Field(default=0.25, gt=0, le=0.25)
    betfair_commission: float = Field(default=0.05, ge=0.0, lt=1.0)

    # --- Telegram (opcional, PHASE 15) -------------------------------------
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ----------------------------------------------------------------------
    @field_validator("data_dir", "models_dir", "logs_dir", mode="after")
    @classmethod
    def _resolve_against_project_root(cls, value: Path) -> Path:
        """Las rutas relativas se resuelven contra la raiz del proyecto.

        Asi el comportamiento no depende del directorio de trabajo desde el que
        se lance la CLI o un servicio systemd.
        """
        return value if value.is_absolute() else (PROJECT_ROOT / value)

    @model_validator(mode="after")
    def _telegram_requires_credentials(self) -> Settings:
        if self.telegram_enabled and not (self.telegram_bot_token and self.telegram_chat_id):
            raise ValueError("TELEGRAM_ENABLED=true requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")
        return self

    @model_validator(mode="after")
    def _max_exposure_is_coherent(self) -> Settings:
        if self.max_market_exposure > self.max_daily_exposure:
            raise ValueError(
                "MAX_MARKET_EXPOSURE no puede superar MAX_DAILY_EXPOSURE "
                f"({self.max_market_exposure} > {self.max_daily_exposure})"
            )
        if self.max_stake_percentage > self.max_market_exposure:
            raise ValueError(
                "MAX_STAKE_PERCENTAGE no puede superar MAX_MARKET_EXPOSURE "
                f"({self.max_stake_percentage} > {self.max_market_exposure})"
            )
        return self

    # --- Rutas derivadas ---------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def odds_dir(self) -> Path:
        return self.data_dir / "odds"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @property
    def production_dir(self) -> Path:
        return self.models_dir / "production"

    @property
    def challenger_dir(self) -> Path:
        return self.models_dir / "challenger"

    def ensure_directories(self) -> None:
        """Crea las rutas de trabajo si no existen. Idempotente."""
        for path in (
            self.raw_dir,
            self.processed_dir,
            self.odds_dir,
            self.results_dir,
            self.production_dir,
            self.challenger_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuracion cacheada del proceso."""
    return Settings()

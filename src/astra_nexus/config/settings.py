from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Astra Nexus"
    environment: str = "local"
    database_url: str = "sqlite:///./data/astra_nexus.sqlite3"
    workspace_base_path: Path = Path("data/workspaces")
    brain_provider: str = "dummy"
    log_level: str = "INFO"
    telegram_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "ASTRA_TELEGRAM_BOT_TOKEN"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ASTRA_",
        extra="ignore",
    )


@lru_cache
def load_settings() -> Settings:
    return Settings()

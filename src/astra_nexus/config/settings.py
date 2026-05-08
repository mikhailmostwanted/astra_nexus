from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Astra Nexus"
    environment: str = Field(
        default="local",
        validation_alias=AliasChoices("ASTRA_ENVIRONMENT", "ENVIRONMENT"),
    )
    data_dir: Path = Field(
        default=Path("./data"),
        validation_alias=AliasChoices("ASTRA_DATA_DIR", "DATA_DIR"),
    )
    database_url: str = Field(
        default="sqlite:///./data/astra_nexus.sqlite3",
        validation_alias=AliasChoices("ASTRA_DATABASE_URL", "DATABASE_URL"),
    )
    workspace_base_path: Path = Field(
        default=Path("data/workspaces"),
        validation_alias=AliasChoices("ASTRA_WORKSPACE_BASE_PATH", "WORKSPACE_BASE_PATH"),
    )
    brain_provider: str = Field(
        default="dummy",
        validation_alias=AliasChoices("ASTRA_BRAIN_PROVIDER", "BRAIN_PROVIDER"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("ASTRA_LOG_LEVEL", "LOG_LEVEL"),
    )
    api_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("ASTRA_API_HOST", "API_HOST"),
    )
    api_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("ASTRA_API_PORT", "API_PORT"),
    )
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

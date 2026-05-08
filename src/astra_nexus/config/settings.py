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
    nodriver_user_data_dir: Path = Field(
        default=Path("./data/browser_profiles/default"),
        validation_alias=AliasChoices("ASTRA_NODRIVER_USER_DATA_DIR", "NODRIVER_USER_DATA_DIR"),
    )
    nodriver_headless: bool = Field(
        default=False,
        validation_alias=AliasChoices("ASTRA_NODRIVER_HEADLESS", "NODRIVER_HEADLESS"),
    )
    nodriver_chatgpt_url: str = Field(
        default="https://chatgpt.com/",
        validation_alias=AliasChoices("ASTRA_NODRIVER_CHATGPT_URL", "NODRIVER_CHATGPT_URL"),
    )
    nodriver_response_timeout_seconds: int = Field(
        default=180,
        validation_alias=AliasChoices(
            "ASTRA_NODRIVER_RESPONSE_TIMEOUT_SECONDS",
            "NODRIVER_RESPONSE_TIMEOUT_SECONDS",
        ),
    )
    nodriver_page_load_timeout_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "ASTRA_NODRIVER_PAGE_LOAD_TIMEOUT_SECONDS",
            "NODRIVER_PAGE_LOAD_TIMEOUT_SECONDS",
        ),
    )
    nodriver_agent_mode: str = Field(
        default="single_profile",
        validation_alias=AliasChoices("ASTRA_NODRIVER_AGENT_MODE", "NODRIVER_AGENT_MODE"),
    )
    nodriver_debug_screenshots: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ASTRA_NODRIVER_DEBUG_SCREENSHOTS",
            "NODRIVER_DEBUG_SCREENSHOTS",
        ),
    )
    nodriver_screenshots_dir: Path = Field(
        default=Path("./data/debug/screenshots"),
        validation_alias=AliasChoices(
            "ASTRA_NODRIVER_SCREENSHOTS_DIR",
            "NODRIVER_SCREENSHOTS_DIR",
        ),
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
        populate_by_name=True,
    )


@lru_cache
def load_settings() -> Settings:
    return Settings()

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Astra Nexus"
    environment: str = Field(
        default="local",
        validation_alias=AliasChoices("environment", "ASTRA_ENVIRONMENT", "ENVIRONMENT"),
    )
    data_dir: Path = Field(
        default=Path("./data"),
        validation_alias=AliasChoices("data_dir", "ASTRA_DATA_DIR", "DATA_DIR"),
    )
    database_url: str = Field(
        default="sqlite:///./data/astra_nexus.sqlite3",
        validation_alias=AliasChoices("database_url", "ASTRA_DATABASE_URL", "DATABASE_URL"),
    )
    workspace_base_path: Path = Field(
        default=Path("data/workspaces"),
        validation_alias=AliasChoices(
            "workspace_base_path",
            "ASTRA_WORKSPACE_BASE_PATH",
            "WORKSPACE_BASE_PATH",
        ),
    )
    brain_provider: str = Field(
        default="dummy",
        validation_alias=AliasChoices("brain_provider", "ASTRA_BRAIN_PROVIDER", "BRAIN_PROVIDER"),
    )
    nodriver_user_data_dir: Path = Field(
        default=Path("./data/browser_profiles/default"),
        validation_alias=AliasChoices(
            "nodriver_user_data_dir",
            "ASTRA_NODRIVER_USER_DATA_DIR",
            "NODRIVER_USER_DATA_DIR",
        ),
    )
    nodriver_headless: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "nodriver_headless",
            "ASTRA_NODRIVER_HEADLESS",
            "NODRIVER_HEADLESS",
        ),
    )
    nodriver_start_timeout_seconds: int = Field(
        default=90,
        validation_alias=AliasChoices(
            "nodriver_start_timeout_seconds",
            "ASTRA_NODRIVER_START_TIMEOUT_SECONDS",
            "NODRIVER_START_TIMEOUT_SECONDS",
        ),
    )
    nodriver_no_sandbox: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "nodriver_no_sandbox",
            "ASTRA_NODRIVER_NO_SANDBOX",
            "NODRIVER_NO_SANDBOX",
        ),
    )
    nodriver_browser_executable_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "nodriver_browser_executable_path",
            "ASTRA_NODRIVER_BROWSER_EXECUTABLE_PATH",
            "NODRIVER_BROWSER_EXECUTABLE_PATH",
        ),
    )
    nodriver_chatgpt_url: str = Field(
        default="https://chatgpt.com/",
        validation_alias=AliasChoices(
            "nodriver_chatgpt_url",
            "ASTRA_NODRIVER_CHATGPT_URL",
            "NODRIVER_CHATGPT_URL",
        ),
    )
    nodriver_response_timeout_seconds: int = Field(
        default=180,
        validation_alias=AliasChoices(
            "nodriver_response_timeout_seconds",
            "ASTRA_NODRIVER_RESPONSE_TIMEOUT_SECONDS",
            "NODRIVER_RESPONSE_TIMEOUT_SECONDS",
        ),
    )
    nodriver_page_load_timeout_seconds: float = Field(
        default=60,
        validation_alias=AliasChoices(
            "nodriver_page_load_timeout_seconds",
            "ASTRA_NODRIVER_PAGE_LOAD_TIMEOUT_SECONDS",
            "NODRIVER_PAGE_LOAD_TIMEOUT_SECONDS",
        ),
    )
    nodriver_keep_browser_open_on_error: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "nodriver_keep_browser_open_on_error",
            "ASTRA_NODRIVER_KEEP_BROWSER_OPEN_ON_ERROR",
            "NODRIVER_KEEP_BROWSER_OPEN_ON_ERROR",
        ),
    )
    nodriver_agent_mode: str = Field(
        default="single_profile",
        validation_alias=AliasChoices(
            "nodriver_agent_mode",
            "ASTRA_NODRIVER_AGENT_MODE",
            "NODRIVER_AGENT_MODE",
        ),
    )
    nodriver_debug_screenshots: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "nodriver_debug_screenshots",
            "ASTRA_NODRIVER_DEBUG_SCREENSHOTS",
            "NODRIVER_DEBUG_SCREENSHOTS",
        ),
    )
    nodriver_screenshots_dir: Path = Field(
        default=Path("./data/debug/screenshots"),
        validation_alias=AliasChoices(
            "nodriver_screenshots_dir",
            "ASTRA_NODRIVER_SCREENSHOTS_DIR",
            "NODRIVER_SCREENSHOTS_DIR",
        ),
    )
    nodriver_start_retry_attempts: int = 3
    nodriver_start_retry_delay_seconds: float = 2.0
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("log_level", "ASTRA_LOG_LEVEL", "LOG_LEVEL"),
    )
    api_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("api_host", "ASTRA_API_HOST", "API_HOST"),
    )
    api_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("api_port", "ASTRA_API_PORT", "API_PORT"),
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "telegram_bot_token",
            "TELEGRAM_BOT_TOKEN",
            "ASTRA_TELEGRAM_BOT_TOKEN",
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ASTRA_",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("nodriver_browser_executable_path", mode="before")
    @classmethod
    def empty_browser_executable_path_as_none(cls, value: object) -> object:
        if value == "":
            return None
        return value


@lru_cache
def load_settings() -> Settings:
    return Settings()

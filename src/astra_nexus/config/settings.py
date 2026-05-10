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
    team_runs_dir: Path = Field(
        default=Path("data/team_runs"),
        validation_alias=AliasChoices(
            "team_runs_dir",
            "TEAM_RUNS_DIR",
            "ASTRA_TEAM_RUNS_DIR",
        ),
    )
    team_agent_max_retries: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "team_agent_max_retries",
            "TEAM_AGENT_MAX_RETRIES",
            "ASTRA_TEAM_AGENT_MAX_RETRIES",
        ),
    )
    team_agent_retry_delay_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices(
            "team_agent_retry_delay_seconds",
            "TEAM_AGENT_RETRY_DELAY_SECONDS",
            "ASTRA_TEAM_AGENT_RETRY_DELAY_SECONDS",
        ),
    )
    team_agent_response_timeout_seconds: float = Field(
        default=240.0,
        validation_alias=AliasChoices(
            "team_agent_response_timeout_seconds",
            "TEAM_AGENT_RESPONSE_TIMEOUT_SECONDS",
            "ASTRA_TEAM_AGENT_RESPONSE_TIMEOUT_SECONDS",
        ),
    )
    team_previous_results_max_chars: int = Field(
        default=16000,
        validation_alias=AliasChoices(
            "team_previous_results_max_chars",
            "TEAM_PREVIOUS_RESULTS_MAX_CHARS",
            "ASTRA_TEAM_PREVIOUS_RESULTS_MAX_CHARS",
        ),
    )
    team_execution_mode: str = Field(
        default="sequential",
        validation_alias=AliasChoices(
            "team_execution_mode",
            "TEAM_EXECUTION_MODE",
            "ASTRA_TEAM_EXECUTION_MODE",
        ),
    )
    team_max_parallel_agents: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "team_max_parallel_agents",
            "TEAM_MAX_PARALLEL_AGENTS",
            "ASTRA_TEAM_MAX_PARALLEL_AGENTS",
        ),
    )
    team_parallel_agent_timeout_seconds: float = Field(
        default=240.0,
        validation_alias=AliasChoices(
            "team_parallel_agent_timeout_seconds",
            "TEAM_PARALLEL_AGENT_TIMEOUT_SECONDS",
            "ASTRA_TEAM_PARALLEL_AGENT_TIMEOUT_SECONDS",
        ),
    )
    team_max_revision_loops: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "team_max_revision_loops",
            "TEAM_MAX_REVISION_LOOPS",
            "ASTRA_TEAM_MAX_REVISION_LOOPS",
        ),
    )
    team_attachments_max_files: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "team_attachments_max_files",
            "TEAM_ATTACHMENTS_MAX_FILES",
            "ASTRA_TEAM_ATTACHMENTS_MAX_FILES",
        ),
    )
    team_attachment_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        validation_alias=AliasChoices(
            "team_attachment_max_bytes",
            "TEAM_ATTACHMENT_MAX_BYTES",
            "ASTRA_TEAM_ATTACHMENT_MAX_BYTES",
        ),
    )
    team_attachment_text_max_chars: int = Field(
        default=20000,
        validation_alias=AliasChoices(
            "team_attachment_text_max_chars",
            "TEAM_ATTACHMENT_TEXT_MAX_CHARS",
            "ASTRA_TEAM_ATTACHMENT_TEXT_MAX_CHARS",
        ),
    )
    team_attachment_max_extracted_chars: int = Field(
        default=50000,
        validation_alias=AliasChoices(
            "team_attachment_max_extracted_chars",
            "TEAM_ATTACHMENT_MAX_EXTRACTED_CHARS",
            "ASTRA_TEAM_ATTACHMENT_MAX_EXTRACTED_CHARS",
        ),
    )
    team_attachment_max_prompt_chars: int = Field(
        default=20000,
        validation_alias=AliasChoices(
            "team_attachment_max_prompt_chars",
            "TEAM_ATTACHMENT_MAX_PROMPT_CHARS",
            "ASTRA_TEAM_ATTACHMENT_MAX_PROMPT_CHARS",
            "TEAM_ATTACHMENT_TEXT_MAX_CHARS",
            "ASTRA_TEAM_ATTACHMENT_TEXT_MAX_CHARS",
        ),
    )
    team_attachment_pdf_max_pages: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "team_attachment_pdf_max_pages",
            "TEAM_ATTACHMENT_PDF_MAX_PAGES",
            "ASTRA_TEAM_ATTACHMENT_PDF_MAX_PAGES",
        ),
    )
    team_attachment_docx_include_tables: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "team_attachment_docx_include_tables",
            "TEAM_ATTACHMENT_DOCX_INCLUDE_TABLES",
            "ASTRA_TEAM_ATTACHMENT_DOCX_INCLUDE_TABLES",
        ),
    )
    team_uploads_dir: Path = Field(
        default=Path("data/team_uploads"),
        validation_alias=AliasChoices(
            "team_uploads_dir",
            "TEAM_UPLOADS_DIR",
            "ASTRA_TEAM_UPLOADS_DIR",
        ),
    )
    team_atmosphere_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "team_atmosphere_enabled",
            "TEAM_ATMOSPHERE_ENABLED",
            "ASTRA_TEAM_ATMOSPHERE_ENABLED",
        ),
    )
    team_atmosphere_level: str = Field(
        default="normal",
        validation_alias=AliasChoices(
            "team_atmosphere_level",
            "TEAM_ATMOSPHERE_LEVEL",
            "ASTRA_TEAM_ATMOSPHERE_LEVEL",
        ),
    )
    team_atmosphere_send_delays: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "team_atmosphere_send_delays",
            "TEAM_ATMOSPHERE_SEND_DELAYS",
            "ASTRA_TEAM_ATMOSPHERE_SEND_DELAYS",
        ),
    )
    team_atmosphere_min_delay_seconds: float = Field(
        default=0.3,
        validation_alias=AliasChoices(
            "team_atmosphere_min_delay_seconds",
            "TEAM_ATMOSPHERE_MIN_DELAY_SECONDS",
            "ASTRA_TEAM_ATMOSPHERE_MIN_DELAY_SECONDS",
        ),
    )
    team_atmosphere_max_delay_seconds: float = Field(
        default=1.4,
        validation_alias=AliasChoices(
            "team_atmosphere_max_delay_seconds",
            "TEAM_ATMOSPHERE_MAX_DELAY_SECONDS",
            "ASTRA_TEAM_ATMOSPHERE_MAX_DELAY_SECONDS",
        ),
    )
    team_atmosphere_emoji_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "team_atmosphere_emoji_enabled",
            "TEAM_ATMOSPHERE_EMOJI_ENABLED",
            "ASTRA_TEAM_ATMOSPHERE_EMOJI_ENABLED",
        ),
    )
    team_atmosphere_max_main_messages_per_run: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "team_atmosphere_max_main_messages_per_run",
            "TEAM_ATMOSPHERE_MAX_MAIN_MESSAGES_PER_RUN",
            "ASTRA_TEAM_ATMOSPHERE_MAX_MAIN_MESSAGES_PER_RUN",
        ),
    )
    team_atmosphere_suppress_technical_in_main: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "team_atmosphere_suppress_technical_in_main",
            "TEAM_ATMOSPHERE_SUPPRESS_TECHNICAL_IN_MAIN",
            "ASTRA_TEAM_ATMOSPHERE_SUPPRESS_TECHNICAL_IN_MAIN",
        ),
    )
    team_telegram_downloads_dir: Path = Field(
        default=Path("data/team_telegram_downloads"),
        validation_alias=AliasChoices(
            "team_telegram_downloads_dir",
            "TEAM_TELEGRAM_DOWNLOADS_DIR",
            "ASTRA_TEAM_TELEGRAM_DOWNLOADS_DIR",
            "TEAM_UPLOADS_DIR",
            "ASTRA_TEAM_UPLOADS_DIR",
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
    nodriver_window_mode: str = Field(
        default="small",
        validation_alias=AliasChoices(
            "nodriver_window_mode",
            "ASTRA_NODRIVER_WINDOW_MODE",
            "NODRIVER_WINDOW_MODE",
        ),
    )
    nodriver_window_width: int = Field(
        default=1100,
        validation_alias=AliasChoices(
            "nodriver_window_width",
            "ASTRA_NODRIVER_WINDOW_WIDTH",
            "NODRIVER_WINDOW_WIDTH",
        ),
    )
    nodriver_window_height: int = Field(
        default=800,
        validation_alias=AliasChoices(
            "nodriver_window_height",
            "ASTRA_NODRIVER_WINDOW_HEIGHT",
            "NODRIVER_WINDOW_HEIGHT",
        ),
    )
    nodriver_window_x: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "nodriver_window_x",
            "ASTRA_NODRIVER_WINDOW_X",
            "NODRIVER_WINDOW_X",
        ),
    )
    nodriver_window_y: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "nodriver_window_y",
            "ASTRA_NODRIVER_WINDOW_Y",
            "NODRIVER_WINDOW_Y",
        ),
    )
    nodriver_background_start: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "nodriver_background_start",
            "ASTRA_NODRIVER_BACKGROUND_START",
            "NODRIVER_BACKGROUND_START",
        ),
    )
    nodriver_disable_focus_stealing: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "nodriver_disable_focus_stealing",
            "ASTRA_NODRIVER_DISABLE_FOCUS_STEALING",
            "NODRIVER_DISABLE_FOCUS_STEALING",
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
    nodriver_start_retry_attempts: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "nodriver_start_retry_attempts",
            "nodriver_start_retries",
            "ASTRA_NODRIVER_START_RETRY_ATTEMPTS",
            "NODRIVER_START_RETRY_ATTEMPTS",
            "ASTRA_NODRIVER_START_RETRIES",
            "NODRIVER_START_RETRIES",
        ),
    )
    nodriver_start_retry_delay_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices(
            "nodriver_start_retry_delay_seconds",
            "nodriver_start_retry_backoff_seconds",
            "ASTRA_NODRIVER_START_RETRY_DELAY_SECONDS",
            "NODRIVER_START_RETRY_DELAY_SECONDS",
            "ASTRA_NODRIVER_START_RETRY_BACKOFF_SECONDS",
            "NODRIVER_START_RETRY_BACKOFF_SECONDS",
        ),
    )
    nodriver_after_terminate_grace_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices(
            "nodriver_after_terminate_grace_seconds",
            "ASTRA_NODRIVER_AFTER_TERMINATE_GRACE_SECONDS",
            "NODRIVER_AFTER_TERMINATE_GRACE_SECONDS",
        ),
    )
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
    team_telegram_provider: str = Field(
        default="fake",
        validation_alias=AliasChoices(
            "team_telegram_provider",
            "TEAM_TELEGRAM_PROVIDER",
            "ASTRA_TEAM_TELEGRAM_PROVIDER",
        ),
    )
    team_telegram_log_chat_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "team_telegram_log_chat_id",
            "TEAM_TELEGRAM_LOG_CHAT_ID",
            "ASTRA_TEAM_TELEGRAM_LOG_CHAT_ID",
        ),
    )
    team_telegram_allowed_chat_ids: str = Field(
        default="",
        validation_alias=AliasChoices(
            "team_telegram_allowed_chat_ids",
            "TEAM_TELEGRAM_ALLOWED_CHAT_IDS",
            "ASTRA_TEAM_TELEGRAM_ALLOWED_CHAT_IDS",
        ),
    )
    team_telegram_send_typing: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "team_telegram_send_typing",
            "TEAM_TELEGRAM_SEND_TYPING",
            "ASTRA_TEAM_TELEGRAM_SEND_TYPING",
        ),
    )
    team_telegram_max_file_size_mb: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "team_telegram_max_file_size_mb",
            "TEAM_TELEGRAM_MAX_FILE_SIZE_MB",
            "ASTRA_TEAM_TELEGRAM_MAX_FILE_SIZE_MB",
        ),
    )
    team_telegram_human_messages: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "team_telegram_human_messages",
            "TEAM_TELEGRAM_HUMAN_MESSAGES",
            "ASTRA_TEAM_TELEGRAM_HUMAN_MESSAGES",
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

    @field_validator("team_atmosphere_level", mode="before")
    @classmethod
    def normalize_team_atmosphere_level(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"minimal", "normal", "cinematic"}:
                return normalized
        return value

    @field_validator("nodriver_window_mode", mode="before")
    @classmethod
    def normalize_nodriver_window_mode(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"normal", "small", "offscreen", "headless"}:
                return normalized
        return value

    @property
    def nodriver_start_retry_backoff_seconds(self) -> float:
        return self.nodriver_start_retry_delay_seconds

    @property
    def nodriver_start_retries(self) -> int:
        return self.nodriver_start_retry_attempts


@lru_cache
def load_settings() -> Settings:
    return Settings()

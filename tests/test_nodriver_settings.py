from astra_nexus.config.settings import Settings


def test_keep_browser_open_on_error_setting_defaults_to_false() -> None:
    settings = Settings(_env_file=None)

    assert settings.nodriver_keep_browser_open_on_error is False


def test_keep_browser_open_on_error_setting_can_be_enabled() -> None:
    settings = Settings(nodriver_keep_browser_open_on_error=True)

    assert settings.nodriver_keep_browser_open_on_error is True


def test_start_retry_backoff_setting_accepts_env_alias(monkeypatch) -> None:
    monkeypatch.setenv("NODRIVER_START_RETRY_BACKOFF_SECONDS", "4.5")

    settings = Settings(_env_file=None)

    assert settings.nodriver_start_retry_delay_seconds == 4.5
    assert settings.nodriver_start_retry_backoff_seconds == 4.5


def test_start_lifecycle_settings_accept_requested_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("NODRIVER_START_RETRIES", "5")
    monkeypatch.setenv("NODRIVER_START_RETRY_DELAY_SECONDS", "3.5")
    monkeypatch.setenv("NODRIVER_AFTER_TERMINATE_GRACE_SECONDS", "1.25")

    settings = Settings(_env_file=None)

    assert settings.nodriver_start_retry_attempts == 5
    assert settings.nodriver_start_retries == 5
    assert settings.nodriver_start_retry_delay_seconds == 3.5
    assert settings.nodriver_after_terminate_grace_seconds == 1.25

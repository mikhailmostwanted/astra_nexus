from astra_nexus.config.settings import Settings


def test_keep_browser_open_on_error_setting_defaults_to_false() -> None:
    settings = Settings()

    assert settings.nodriver_keep_browser_open_on_error is False


def test_keep_browser_open_on_error_setting_can_be_enabled() -> None:
    settings = Settings(nodriver_keep_browser_open_on_error=True)

    assert settings.nodriver_keep_browser_open_on_error is True

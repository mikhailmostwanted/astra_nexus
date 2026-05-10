from __future__ import annotations

from astra_nexus.config.settings import Settings

MANUAL_VISIBLE_CONTEXTS = {"login", "dom_probe", "insert_probe"}
OFFSCREEN_POSITION = "-32000,-32000"


def effective_nodriver_window_mode(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> str:
    if fallback_window_mode:
        return "small" if context in MANUAL_VISIBLE_CONTEXTS else "normal"

    mode = settings.nodriver_window_mode.strip().lower()
    if context in MANUAL_VISIBLE_CONTEXTS and mode in {"offscreen", "headless"}:
        return "small"
    return mode


def effective_nodriver_headless(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> bool:
    if fallback_window_mode:
        return False
    if context in MANUAL_VISIBLE_CONTEXTS:
        return False
    if settings.nodriver_headless:
        return True
    return (
        effective_nodriver_window_mode(
            settings,
            context=context,
            fallback_window_mode=fallback_window_mode,
        )
        == "headless"
    )


def build_nodriver_browser_args(
    settings: Settings,
    *,
    context: str = "provider",
    fallback_window_mode: bool = False,
) -> list[str]:
    mode = effective_nodriver_window_mode(
        settings,
        context=context,
        fallback_window_mode=fallback_window_mode,
    )
    if mode in {"normal", "headless"} or effective_nodriver_headless(
        settings,
        context=context,
        fallback_window_mode=fallback_window_mode,
    ):
        return []

    width = max(320, int(settings.nodriver_window_width))
    height = max(240, int(settings.nodriver_window_height))
    if mode == "offscreen":
        position = OFFSCREEN_POSITION
    else:
        position = f"{int(settings.nodriver_window_x)},{int(settings.nodriver_window_y)}"

    return [
        f"--window-size={width},{height}",
        f"--window-position={position}",
    ]

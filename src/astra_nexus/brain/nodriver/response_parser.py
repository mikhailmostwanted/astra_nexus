from __future__ import annotations

from astra_nexus.brain.nodriver.exceptions import NoDriverSelectorNotFoundError


def parse_last_assistant_response(messages: list[str]) -> str:
    cleaned = [message.strip() for message in messages if message.strip()]
    if not cleaned:
        raise NoDriverSelectorNotFoundError("Не найден ответ ассистента ChatGPT.")
    return cleaned[-1]

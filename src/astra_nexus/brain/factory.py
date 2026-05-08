from __future__ import annotations

from astra_nexus.brain.base import BrainProvider
from astra_nexus.brain.dummy_provider import DummyBrainProvider
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings


def build_brain_provider(settings: Settings) -> BrainProvider:
    provider_name = settings.brain_provider.strip().lower()
    match provider_name:
        case "dummy":
            return DummyBrainProvider()
        case "nodriver":
            return NoDriverProvider(settings=settings)
        case unknown:
            raise ValueError(f"Неизвестный brain-provider: {unknown}")

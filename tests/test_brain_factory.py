import pytest

from astra_nexus.brain.dummy_provider import DummyBrainProvider
from astra_nexus.brain.factory import build_brain_provider
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings


def test_brain_factory_selects_dummy_provider() -> None:
    settings = Settings(brain_provider="dummy")

    provider = build_brain_provider(settings)

    assert isinstance(provider, DummyBrainProvider)


def test_brain_factory_selects_nodriver_provider() -> None:
    settings = Settings(brain_provider="nodriver")

    provider = build_brain_provider(settings)

    assert isinstance(provider, NoDriverProvider)


def test_brain_factory_rejects_unknown_provider() -> None:
    settings = Settings(brain_provider="unknown")

    with pytest.raises(ValueError, match="Неизвестный brain-provider"):
        build_brain_provider(settings)

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from astra_nexus.brain.dummy_provider import DummyBrainProvider
from astra_nexus.brain.nodriver.health import BrainHealth, check_nodriver_health
from astra_nexus.brain.nodriver_provider import NoDriverProvider

router = APIRouter(prefix="/api/brain", tags=["brain"])


@router.get("/health")
def brain_health(request: Request) -> dict[str, Any]:
    provider = request.app.state.brain_provider
    if isinstance(provider, DummyBrainProvider):
        return BrainHealth(
            status="ok",
            provider="dummy",
            message="DummyBrainProvider готов к работе.",
        ).as_dict()
    if isinstance(provider, NoDriverProvider):
        return asyncio.run(check_nodriver_health(provider.settings)).as_dict()
    return {
        "status": "unavailable",
        "provider": getattr(provider, "name", "unknown"),
        "message": "Неизвестный brain-provider.",
    }

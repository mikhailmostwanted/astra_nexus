from __future__ import annotations

import uvicorn

from astra_nexus.api.app import create_app
from astra_nexus.config.settings import load_settings

app = create_app()


def run_api() -> None:
    settings = load_settings()
    uvicorn.run(
        "astra_nexus.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.environment == "local",
    )


if __name__ == "__main__":
    run_api()

"""
Litestar application setup for device HTTP server.

Note: The admin frontend is served by Cloudflare Pages at /admin/.
This server only handles API endpoints and HLS streaming.
"""

import logging

from litestar import Litestar, get
from litestar.config.cors import CORSConfig
from litestar.di import Provide

from .routes import (
    AdminController,
    CamerasController,
    StreamsController,
    LogsController,
    HLSController,
    CourtsController,
)
from ..streaming import StreamManager

logger = logging.getLogger(__name__)


def create_app(
    stream_manager: StreamManager,
) -> Litestar:
    """
    Create and configure the Litestar application.

    Args:
        stream_manager: StreamManager instance for camera/stream operations

    Returns:
        Configured Litestar application
    """

    # Dependency providers
    async def provide_stream_manager() -> StreamManager:
        return stream_manager

    # Health check endpoint (outside controllers)
    @get("/health", exclude_from_auth=True)
    async def health_check() -> dict:
        return {"status": "ok"}

    # CORS config for Cloudflare frontend
    cors_config = CORSConfig(
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app = Litestar(
        route_handlers=[
            health_check,
            AdminController,
            CamerasController,
            StreamsController,
            LogsController,
            HLSController,
            CourtsController,
        ],
        dependencies={
            "stream_manager": Provide(provide_stream_manager),
        },
        cors_config=cors_config,
        debug=False,
    )

    return app

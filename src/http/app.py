"""
Litestar application setup for device HTTP server.

Note: The admin frontend is served by Cloudflare Pages at /admin/.
This server only handles API endpoints and HLS streaming.
"""

import logging
import time

from litestar import Litestar, get, Request
from litestar.config.cors import CORSConfig
from litestar.di import Provide
from litestar.middleware import AbstractMiddleware
from litestar.types import ASGIApp, Receive, Scope, Send

from .routes import (
    AdminController,
    CamerasController,
    StreamsController,
    LogsController,
    DeviceLogsController,
    HLSController,
    APIHLSController,
    CourtsController,
)
from ..streaming import StreamManager

logger = logging.getLogger(__name__)
http_logger = logging.getLogger("http.requests")

# Paths to exclude from logging (noisy endpoints)
EXCLUDED_PATHS = {
    "/health",
    "/api/logs/stream",  # SSE stream - would log too much
}

# Path prefixes to exclude (HLS segments are very frequent)
EXCLUDED_PREFIXES = (
    "/hls/",
    "/api/hls/",
)


class RequestLoggingMiddleware(AbstractMiddleware):
    """Middleware to log HTTP requests."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip excluded paths
        if path in EXCLUDED_PATHS or path.startswith(EXCLUDED_PREFIXES):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        start_time = time.time()

        # Capture response status
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.time() - start_time) * 1000
            # Log format: METHOD /path STATUS DURATIONms
            http_logger.info(f"{method} {path} {status_code} {duration_ms:.1f}ms")


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
            DeviceLogsController,
            HLSController,
            APIHLSController,
            CourtsController,
        ],
        dependencies={
            "stream_manager": Provide(provide_stream_manager),
        },
        middleware=[RequestLoggingMiddleware],
        cors_config=cors_config,
        debug=False,
    )

    return app

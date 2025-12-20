"""
Litestar application setup for device HTTP server.
"""

import logging
from pathlib import Path
from typing import Optional

from litestar import Litestar, get
from litestar.config.cors import CORSConfig
from litestar.di import Provide
from litestar.response import Redirect, File
from litestar.static_files import StaticFilesConfig

from .routes import (
    AdminController,
    CamerasController,
    StreamsController,
    ButtonsController,
    LogsController,
    HLSController,
    CourtsController,
)
from ..streaming import StreamManager

logger = logging.getLogger(__name__)

# Static files directory (Vue.js build output)
STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    stream_manager: StreamManager,
    gpio_handler: Optional[object] = None,
) -> Litestar:
    """
    Create and configure the Litestar application.

    Args:
        stream_manager: StreamManager instance for camera/stream operations
        gpio_handler: Optional GPIO button handler

    Returns:
        Configured Litestar application
    """

    # Dependency providers
    async def provide_stream_manager() -> StreamManager:
        return stream_manager

    async def provide_gpio_handler() -> Optional[object]:
        return gpio_handler

    # Health check endpoint (outside controllers)
    @get("/health", exclude_from_auth=True)
    async def health_check() -> dict:
        return {"status": "ok"}

    # Serve frontend index
    @get("/", exclude_from_auth=True)
    async def root_redirect() -> Redirect:
        return Redirect(path="/admin/")

    @get("/admin/", exclude_from_auth=True)
    async def admin_index() -> File:
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return File(path=index_file, media_type="text/html")
        # Return empty response if no frontend built yet
        from litestar.response import Response
        return Response(
            content="<html><body><h1>Admin not built</h1></body></html>",
            media_type="text/html",
        )

    # Static files config for frontend assets
    static_files_config = []
    if STATIC_DIR.exists():
        static_files_config.append(
            StaticFilesConfig(
                directories=[STATIC_DIR],
                path="/admin/assets",
                name="static",
            )
        )

    # CORS config for development
    cors_config = CORSConfig(
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app = Litestar(
        route_handlers=[
            health_check,
            root_redirect,
            admin_index,
            AdminController,
            CamerasController,
            StreamsController,
            ButtonsController,
            LogsController,
            HLSController,
            CourtsController,
        ],
        dependencies={
            "stream_manager": Provide(provide_stream_manager),
            "gpio_handler": Provide(provide_gpio_handler),
        },
        static_files_config=static_files_config,
        cors_config=cors_config,
        debug=False,
    )

    return app

"""
Litestar application setup for device HTTP server.
"""

import logging
from pathlib import Path

from litestar import Litestar, get
from litestar.config.cors import CORSConfig
from litestar.di import Provide
from litestar.response import Redirect, Response
from litestar.static_files import StaticFilesConfig

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

# Static files directory (Vue.js build output)
STATIC_DIR = Path(__file__).parent / "static"


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

    # Serve frontend index
    @get("/", exclude_from_auth=True)
    async def root_redirect() -> Redirect:
        return Redirect(path="/admin/")

    @get("/admin/", exclude_from_auth=True)
    async def admin_index() -> Response:
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            content = index_file.read_text()
            return Response(content=content, media_type="text/html")
        # Return empty response if no frontend built yet
        return Response(
            content="<html><body><h1>Admin not built</h1></body></html>",
            media_type="text/html",
        )

    # Static files config for frontend assets
    static_files_config = []
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        static_files_config.append(
            StaticFilesConfig(
                directories=[assets_dir],
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
            LogsController,
            HLSController,
            CourtsController,
        ],
        dependencies={
            "stream_manager": Provide(provide_stream_manager),
        },
        static_files_config=static_files_config,
        cors_config=cors_config,
        debug=False,
    )

    return app

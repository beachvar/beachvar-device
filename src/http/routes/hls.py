"""
HLS streaming routes.
"""

import logging
from pathlib import Path

from litestar import Controller, get
from litestar.exceptions import HTTPException
from litestar.response import File

logger = logging.getLogger(__name__)

# HLS output directory
HLS_DIR = Path("/tmp/hls")


def _serve_hls_file(camera_id: str, filename: str) -> File:
    """
    Common logic to serve HLS files.
    Returns a File response for m3u8 playlists and .ts segments.
    """
    # Security: only allow m3u8 and ts files
    if not (filename.endswith(".m3u8") or filename.endswith(".ts")):
        raise HTTPException(status_code=400, detail="Invalid file type")

    # Security: prevent path traversal
    if ".." in camera_id or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = HLS_DIR / camera_id / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine content type
    content_type = (
        "application/vnd.apple.mpegurl"
        if filename.endswith(".m3u8")
        else "video/MP2T"
    )

    return File(
        path=file_path,
        media_type=content_type,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


class HLSController(Controller):
    """HLS streaming endpoints (public - security handled by Cloudflare Snippet)."""

    path = "/hls"

    @get("/{camera_id:str}/{filename:str}")
    async def get_hls_file(self, camera_id: str, filename: str) -> File:
        """
        Serve HLS files (m3u8 playlist and .ts segments).

        Security Note:
        - Files are served publicly without authentication
        - Security is handled by Cloudflare Snippet which validates HMAC signatures
        - Backend generates signed URLs, Cloudflare validates them at the edge
        """
        return _serve_hls_file(camera_id, filename)


class APIHLSController(Controller):
    """HLS streaming via /api/hls/ (protected by Cloudflare Zero Trust)."""

    path = "/api/hls"

    @get("/{camera_id:str}/{filename:str}")
    async def get_hls_file(self, camera_id: str, filename: str) -> File:
        """
        Serve HLS files via API path.

        Security Note:
        - This endpoint is protected by Cloudflare Zero Trust (no signature needed)
        - Used by device-frontend admin panel
        """
        return _serve_hls_file(camera_id, filename)

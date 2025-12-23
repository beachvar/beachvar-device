"""
Camera CRUD routes.
"""

from litestar import Controller, get, post, patch, delete
from litestar.exceptions import HTTPException
from pydantic import BaseModel
from typing import Optional

from ...streaming import StreamManager


class CameraCreateDTO(BaseModel):
    """DTO for creating a camera."""
    name: str
    rtsp_url: str
    court_id: str


class CameraUpdateDTO(BaseModel):
    """DTO for updating a camera."""
    name: Optional[str] = None
    rtsp_url: Optional[str] = None


def _get_hls_url(cam, is_connected: bool) -> str:
    """Get local HLS URL for a camera when connected."""
    if is_connected:
        # Uses /api/hls/ which is protected by Cloudflare Zero Trust (no signature needed)
        return f"/api/hls/{cam.id}/playlist.m3u8"
    return ""


class CamerasController(Controller):
    """Camera management endpoints."""

    path = "/api/cameras"

    @get("/")
    async def list_cameras(self, stream_manager: StreamManager) -> dict:
        """List all registered cameras."""
        cameras = await stream_manager.refresh_cameras()

        return {
            "cameras": [
                {
                    "id": cam.id,
                    "name": cam.name,
                    "rtsp_url": cam.rtsp_url,
                    "court_id": cam.court_id,
                    "court_name": cam.court_name,
                    "complex_id": cam.complex_id,
                    "complex_name": cam.complex_name,
                    "hls_url": _get_hls_url(cam, cam.id in stream_manager.active_streams),
                    "is_connected": cam.id in stream_manager.active_streams,
                    "last_seen_at": cam.last_seen_at,
                    "connection_error": None,
                }
                for cam in cameras
            ],
            "total": len(cameras),
        }

    @get("/{camera_id:str}")
    async def get_camera(self, camera_id: str, stream_manager: StreamManager) -> dict:
        """Get a specific camera."""
        camera = await stream_manager.get_camera(camera_id)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")

        is_connected = camera.id in stream_manager.active_streams
        return {
            "id": camera.id,
            "name": camera.name,
            "rtsp_url": camera.rtsp_url,
            "court_id": camera.court_id,
            "court_name": camera.court_name,
            "complex_id": camera.complex_id,
            "complex_name": camera.complex_name,
            "hls_url": _get_hls_url(camera, is_connected),
            "is_connected": is_connected,
            "last_seen_at": camera.last_seen_at,
            "connection_error": None,
        }

    @post("/")
    async def create_camera(
        self, data: CameraCreateDTO, stream_manager: StreamManager
    ) -> dict:
        """Create a new camera."""
        camera = await stream_manager.create_camera(
            name=data.name,
            rtsp_url=data.rtsp_url,
            court_id=data.court_id,
        )

        if not camera:
            raise HTTPException(status_code=500, detail="Failed to create camera")

        return {
            "id": camera.id,
            "name": camera.name,
            "rtsp_url": camera.rtsp_url,
            "court_id": camera.court_id,
            "court_name": camera.court_name,
            "complex_id": camera.complex_id,
            "complex_name": camera.complex_name,
            "hls_url": "",
            "is_connected": False,
        }

    @patch("/{camera_id:str}")
    async def update_camera(
        self, camera_id: str, data: CameraUpdateDTO, stream_manager: StreamManager
    ) -> dict:
        """Update a camera."""
        # Build update payload with only provided fields
        update_data = {}
        if data.name is not None:
            update_data["name"] = data.name
        if data.rtsp_url is not None:
            update_data["rtsp_url"] = data.rtsp_url

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        camera = await stream_manager.update_camera(camera_id, update_data)
        if not camera:
            raise HTTPException(status_code=500, detail="Failed to update camera")

        return {
            "id": camera.id,
            "name": camera.name,
            "rtsp_url": camera.rtsp_url,
            "court_id": camera.court_id,
            "court_name": camera.court_name,
        }

    @delete("/{camera_id:str}")
    async def delete_camera(
        self, camera_id: str, stream_manager: StreamManager
    ) -> None:
        """Delete a camera."""
        success = await stream_manager.delete_camera(camera_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete camera")
        return None

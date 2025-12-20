"""
Camera CRUD routes.
"""

from litestar import Controller, get, post, patch, delete
from litestar.exceptions import HTTPException
from pydantic import BaseModel
from typing import Optional

from ...streaming import StreamManager
from ...streaming.camera import StreamMode


class CameraCreateDTO(BaseModel):
    """DTO for creating a camera."""
    name: str
    rtsp_url: str
    court_id: str
    position: str = "other"


class CameraUpdateDTO(BaseModel):
    """DTO for updating a camera."""
    name: Optional[str] = None
    rtsp_url: Optional[str] = None
    position: Optional[str] = None


def _get_stream_data(cam, is_streaming: bool) -> dict | None:
    """
    Get stream data for a camera.
    For Cloudflare mode: returns Cloudflare Stream URLs
    For local_hls mode: returns local HLS URL when streaming
    """
    # Cloudflare mode: return stream config if available
    if cam.stream_mode == StreamMode.CLOUDFLARE and cam.stream:
        return {
            "live_input_id": cam.stream.live_input_id,
            "playback_hls": cam.stream.playback_hls,
            "playback_dash": cam.stream.playback_dash,
        }

    # Local HLS mode: return local URL when streaming
    # Uses /api/hls/ which is protected by Cloudflare Zero Trust (no signature needed)
    if cam.stream_mode == StreamMode.LOCAL_HLS and is_streaming:
        return {
            "live_input_id": None,
            "playback_hls": f"/api/hls/{cam.id}/playlist.m3u8",
            "playback_dash": None,
        }

    return None


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
                    "position": cam.position,
                    "court_id": cam.court_id,
                    "court_name": cam.court_name,
                    "complex_id": cam.complex_id,
                    "complex_name": cam.complex_name,
                    "has_stream": cam.has_stream,
                    "stream_mode": cam.stream_mode.value if cam.stream_mode else None,
                    "stream": _get_stream_data(cam, cam.id in stream_manager.active_streams),
                    "is_streaming": cam.id in stream_manager.active_streams,
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

        is_streaming = camera.id in stream_manager.active_streams
        return {
            "id": camera.id,
            "name": camera.name,
            "rtsp_url": camera.rtsp_url,
            "position": camera.position,
            "court_id": camera.court_id,
            "court_name": camera.court_name,
            "complex_id": camera.complex_id,
            "complex_name": camera.complex_name,
            "has_stream": camera.has_stream,
            "stream_mode": camera.stream_mode.value if camera.stream_mode else None,
            "stream": _get_stream_data(camera, is_streaming),
            "is_streaming": is_streaming,
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
            position=data.position,
        )

        if not camera:
            raise HTTPException(status_code=500, detail="Failed to create camera")

        return {
            "id": camera.id,
            "name": camera.name,
            "rtsp_url": camera.rtsp_url,
            "position": camera.position,
            "court_id": camera.court_id,
            "court_name": camera.court_name,
            "complex_id": camera.complex_id,
            "complex_name": camera.complex_name,
            "has_stream": camera.has_stream,
            "stream": {
                "live_input_id": camera.stream.live_input_id,
                "rtmps_url": camera.stream.rtmps_url,
                "playback_hls": camera.stream.playback_hls,
                "playback_dash": camera.stream.playback_dash,
            } if camera.stream else None,
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
        if data.position is not None:
            update_data["position"] = data.position

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        camera = await stream_manager.update_camera(camera_id, update_data)
        if not camera:
            raise HTTPException(status_code=500, detail="Failed to update camera")

        return {
            "id": camera.id,
            "name": camera.name,
            "rtsp_url": camera.rtsp_url,
            "position": camera.position,
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

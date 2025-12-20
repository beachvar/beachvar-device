"""
Stream control routes.
"""

from litestar import Controller, get, post
from litestar.exceptions import HTTPException

from ...streaming import StreamManager


class StreamsController(Controller):
    """Stream control endpoints."""

    path = "/api/streams"

    @get("/")
    async def list_streams(self, stream_manager: StreamManager) -> dict:
        """List all active streams."""
        streams = await stream_manager.get_all_streams()

        return {
            "streams": [
                {
                    "id": s.id,
                    "status": s.status,
                    "started_at": s.started_at,
                    "stopped_at": s.stopped_at,
                    "duration_seconds": s.duration_seconds,
                    "bitrate_kbps": s.bitrate_kbps,
                    "viewers_count": s.viewers_count,
                    "error_message": s.error_message,
                    "is_active": s.is_active,
                }
                for s in streams
            ],
            "total": len(streams),
            "active_count": sum(1 for s in streams if s.is_active),
        }

    @post("/{camera_id:str}/start")
    async def start_stream(
        self, camera_id: str, stream_manager: StreamManager
    ) -> dict:
        """Start streaming from a camera."""
        stream_info = await stream_manager.start_stream(camera_id)
        if not stream_info:
            raise HTTPException(status_code=500, detail="Failed to start stream")

        return {
            "id": stream_info.id,
            "status": stream_info.status,
            "started_at": stream_info.started_at,
            "message": "Stream started successfully",
        }

    @post("/{camera_id:str}/stop")
    async def stop_stream(
        self, camera_id: str, stream_manager: StreamManager
    ) -> dict:
        """Stop streaming from a camera."""
        success = await stream_manager.stop_stream(camera_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="No active stream found or failed to stop"
            )

        return {"message": "Stream stopped successfully"}

    @get("/{camera_id:str}/status")
    async def get_stream_status(
        self, camera_id: str, stream_manager: StreamManager
    ) -> dict:
        """Get stream status for a camera."""
        stream_info = await stream_manager.get_stream_status(camera_id)
        if not stream_info:
            return {
                "status": "idle",
                "message": "No active stream",
            }

        return {
            "id": stream_info.id,
            "status": stream_info.status,
            "started_at": stream_info.started_at,
            "stopped_at": stream_info.stopped_at,
            "duration_seconds": stream_info.duration_seconds,
            "bitrate_kbps": stream_info.bitrate_kbps,
            "viewers_count": stream_info.viewers_count,
            "error_message": stream_info.error_message,
            "is_active": stream_info.is_active,
        }

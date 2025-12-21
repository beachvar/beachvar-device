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
        active_streams = stream_manager.get_active_streams()

        return {
            "streams": [
                {
                    "camera_id": stream.camera_id,
                    "camera_name": stream.camera_name,
                    "is_running": stream.is_running,
                    "started_at": stream.started_at,
                }
                for stream in active_streams
            ],
            "total": len(active_streams),
            "active_count": sum(1 for s in active_streams if s.is_running),
        }

    @post("/{camera_id:str}/start")
    async def start_stream(
        self, camera_id: str, stream_manager: StreamManager
    ) -> dict:
        """Start streaming from a camera."""
        success = await stream_manager.start_stream(camera_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to start stream")

        return {
            "camera_id": camera_id,
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

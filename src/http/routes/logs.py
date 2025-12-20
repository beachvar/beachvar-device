"""
FFmpeg logs routes with SSE support.
"""

import json
from typing import AsyncGenerator

from litestar import Controller, get, delete
from litestar.response import Stream

from ...streaming.logs import log_manager


class LogsController(Controller):
    """FFmpeg logs endpoints with SSE support."""

    path = "/api/cameras"

    @get("/{camera_id:str}/logs")
    async def get_logs(self, camera_id: str) -> dict:
        """Get all logs for a camera."""
        logs = await log_manager.get_logs(camera_id)
        return {
            "camera_id": camera_id,
            "logs": logs,
            "total": len(logs),
        }

    @delete("/{camera_id:str}/logs", status_code=200)
    async def clear_logs(self, camera_id: str) -> dict:
        """Clear logs for a camera."""
        success = await log_manager.clear_logs(camera_id)
        return {
            "camera_id": camera_id,
            "cleared": success,
        }

    @get("/{camera_id:str}/logs/stream")
    async def stream_logs(self, camera_id: str) -> Stream:
        """
        Stream logs in real-time via Server-Sent Events (SSE).

        Usage in frontend:
            const eventSource = new EventSource('/api/cameras/{id}/logs/stream');
            eventSource.onmessage = (e) => {
                const log = JSON.parse(e.data);
                console.log(log);
            };
        """
        async def generate_events() -> AsyncGenerator[bytes, None]:
            # Send initial connection message
            yield b"event: connected\ndata: {\"status\": \"connected\"}\n\n"

            # Stream logs as they arrive
            async for entry in log_manager.subscribe(camera_id):
                data = json.dumps(entry.to_dict())
                yield f"data: {data}\n\n".encode("utf-8")

        return Stream(
            generate_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

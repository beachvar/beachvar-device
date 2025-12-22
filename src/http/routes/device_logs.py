"""
Device-wide logs routes with SSE support.
"""

import json
from typing import AsyncGenerator

from litestar import Controller, get, delete
from litestar.response import Stream

from ...streaming.device_logs import device_log_manager


class DeviceLogsController(Controller):
    """Device-wide logs endpoints with SSE support."""

    path = "/api/logs"

    @get("")
    async def get_logs(
        self,
        level: str | None = None,
        logger: str | None = None,
        limit: int = 500,
    ) -> dict:
        """
        Get device logs with optional filtering.

        Query params:
            level: Filter by log level (debug, info, warning, error)
            logger: Filter by logger name (prefix match)
            limit: Maximum entries to return (default 500)
        """
        logs = await device_log_manager.get_logs(
            level=level,
            logger_name=logger,
            limit=limit,
        )
        return {
            "logs": logs,
            "total": len(logs),
            "filters": {
                "level": level,
                "logger": logger,
                "limit": limit,
            },
        }

    @get("/stats")
    async def get_stats(self) -> dict:
        """Get log statistics."""
        return await device_log_manager.get_stats()

    @delete("", status_code=200)
    async def clear_logs(self) -> dict:
        """Clear all device logs."""
        success = await device_log_manager.clear()
        return {
            "cleared": success,
        }

    @get("/stream")
    async def stream_logs(self) -> Stream:
        """
        Stream device logs in real-time via Server-Sent Events (SSE).

        Usage in frontend:
            const eventSource = new EventSource('/api/logs/stream');
            eventSource.onmessage = (e) => {
                const log = JSON.parse(e.data);
                console.log(log);
            };
        """
        async def generate_events() -> AsyncGenerator[bytes, None]:
            # Send initial connection message
            yield b"event: connected\ndata: {\"status\": \"connected\"}\n\n"

            # Stream logs as they arrive
            async for entry in device_log_manager.subscribe():
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

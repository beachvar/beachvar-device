"""
FFmpeg log manager for capturing and storing camera stream logs.

IMPORTANT: All logs are stored IN MEMORY ONLY using circular buffers.
No disk storage is used to prevent filling up the Raspberry Pi's storage.

Limits:
- Max 500 entries per camera (configurable)
- Max 500 chars per log message (truncated if longer)
- Max 10 cameras with logs at once (oldest removed if exceeded)
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Deque

# Memory-safe limits
MAX_ENTRIES_PER_CAMERA = 500
MAX_MESSAGE_LENGTH = 500
MAX_CAMERAS_WITH_LOGS = 10


@dataclass
class LogEntry:
    """A single log entry."""
    timestamp: datetime
    message: str
    level: str = "info"  # info, warning, error

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "level": self.level,
        }


@dataclass
class CameraLogs:
    """Log buffer for a single camera."""
    camera_id: str
    camera_name: str
    entries: Deque[LogEntry] = field(
        default_factory=lambda: deque(maxlen=MAX_ENTRIES_PER_CAMERA)
    )
    subscribers: list = field(default_factory=list)
    last_activity: datetime = field(default_factory=datetime.now)

    def add(self, message: str, level: str = "info") -> LogEntry:
        """Add a log entry (message truncated if too long)."""
        # Truncate message to prevent memory bloat
        if len(message) > MAX_MESSAGE_LENGTH:
            message = message[:MAX_MESSAGE_LENGTH] + "..."

        entry = LogEntry(
            timestamp=datetime.now(),
            message=message,
            level=level,
        )
        self.entries.append(entry)
        self.last_activity = datetime.now()
        return entry

    def get_all(self) -> list[dict]:
        """Get all log entries as dicts."""
        return [e.to_dict() for e in self.entries]

    def clear(self) -> None:
        """Clear all log entries."""
        self.entries.clear()


class FFmpegLogManager:
    """
    Manager for FFmpeg logs across all cameras.

    Features:
    - Circular buffer (last 500 entries per camera) - IN MEMORY ONLY
    - Max 10 cameras tracked (oldest inactive removed)
    - Thread-safe with asyncio locks
    - SSE subscriber support for real-time streaming
    - NO DISK STORAGE to prevent filling up Raspberry Pi
    """

    def __init__(self):
        self._cameras: dict[str, CameraLogs] = {}
        self._lock = asyncio.Lock()

    def _evict_oldest_camera(self) -> None:
        """Remove oldest inactive camera to stay within limits."""
        if len(self._cameras) < MAX_CAMERAS_WITH_LOGS:
            return

        # Find camera with oldest activity
        oldest_id = min(
            self._cameras.keys(),
            key=lambda cid: self._cameras[cid].last_activity
        )
        del self._cameras[oldest_id]

    async def init_camera(self, camera_id: str, camera_name: str) -> None:
        """Initialize log buffer for a camera."""
        async with self._lock:
            if camera_id not in self._cameras:
                self._evict_oldest_camera()
                self._cameras[camera_id] = CameraLogs(
                    camera_id=camera_id,
                    camera_name=camera_name,
                )

    async def add_log(
        self,
        camera_id: str,
        message: str,
        level: str = "info",
        camera_name: str = ""
    ) -> None:
        """Add a log entry for a camera (in memory only)."""
        async with self._lock:
            if camera_id not in self._cameras:
                self._evict_oldest_camera()
                self._cameras[camera_id] = CameraLogs(
                    camera_id=camera_id,
                    camera_name=camera_name or camera_id,
                )

            entry = self._cameras[camera_id].add(message, level)

            # Notify SSE subscribers
            for queue in self._cameras[camera_id].subscribers:
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    pass  # Skip if subscriber queue is full

    async def get_logs(self, camera_id: str) -> list[dict]:
        """Get all logs for a camera."""
        async with self._lock:
            if camera_id not in self._cameras:
                return []
            return self._cameras[camera_id].get_all()

    async def clear_logs(self, camera_id: str) -> bool:
        """Clear logs for a camera."""
        async with self._lock:
            if camera_id not in self._cameras:
                return False
            self._cameras[camera_id].clear()
            return True

    async def subscribe(self, camera_id: str) -> AsyncIterator[LogEntry]:
        """
        Subscribe to real-time logs for a camera via SSE.

        Usage:
            async for entry in log_manager.subscribe(camera_id):
                yield f"data: {json.dumps(entry.to_dict())}\n\n"
        """
        # Small queue to prevent memory buildup if consumer is slow
        queue: asyncio.Queue[LogEntry] = asyncio.Queue(maxsize=50)

        async with self._lock:
            if camera_id not in self._cameras:
                self._evict_oldest_camera()
                self._cameras[camera_id] = CameraLogs(
                    camera_id=camera_id,
                    camera_name=camera_id,
                )
            self._cameras[camera_id].subscribers.append(queue)

        try:
            while True:
                entry = await queue.get()
                yield entry
        finally:
            async with self._lock:
                if camera_id in self._cameras:
                    try:
                        self._cameras[camera_id].subscribers.remove(queue)
                    except ValueError:
                        pass

    async def get_camera_ids(self) -> list[str]:
        """Get all camera IDs with logs."""
        async with self._lock:
            return list(self._cameras.keys())

    async def remove_camera(self, camera_id: str) -> None:
        """Remove a camera's logs completely."""
        async with self._lock:
            if camera_id in self._cameras:
                del self._cameras[camera_id]


# Global log manager instance
log_manager = FFmpegLogManager()

"""
Device log manager for capturing and storing all device logs.

IMPORTANT: All logs are stored IN MEMORY ONLY using circular buffers.
No disk storage is used to prevent filling up the Raspberry Pi's storage.

Limits:
- Max 1000 entries total (configurable)
- Max 500 chars per log message (truncated if longer)
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Deque


# Memory-safe limits
MAX_ENTRIES = 1000
MAX_MESSAGE_LENGTH = 500


@dataclass
class DeviceLogEntry:
    """A single device log entry."""
    timestamp: datetime
    message: str
    level: str  # debug, info, warning, error
    logger_name: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "level": self.level,
            "logger": self.logger_name,
        }


class DeviceLogHandler(logging.Handler):
    """Custom logging handler that stores logs in memory."""

    def __init__(self, log_manager: "DeviceLogManager"):
        super().__init__()
        self.log_manager = log_manager

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            # Truncate message to prevent memory bloat
            if len(message) > MAX_MESSAGE_LENGTH:
                message = message[:MAX_MESSAGE_LENGTH] + "..."

            entry = DeviceLogEntry(
                timestamp=datetime.fromtimestamp(record.created),
                message=message,
                level=record.levelname.lower(),
                logger_name=record.name,
            )

            # Use thread-safe add (non-async version for logging handler)
            self.log_manager.add_sync(entry)
        except Exception:
            self.handleError(record)


class DeviceLogManager:
    """
    Manager for device-wide logs.

    Features:
    - Circular buffer (last 1000 entries) - IN MEMORY ONLY
    - Thread-safe with asyncio locks
    - SSE subscriber support for real-time streaming
    - Captures all Python logging output
    - NO DISK STORAGE to prevent filling up Raspberry Pi
    """

    def __init__(self):
        self._entries: Deque[DeviceLogEntry] = deque(maxlen=MAX_ENTRIES)
        self._lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._subscribers: list[asyncio.Queue] = []
        self._handler: DeviceLogHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def setup(self) -> None:
        """
        Setup the log handler to capture Python logging.
        Call this after the event loop is running.
        """
        if self._handler is not None:
            return  # Already setup

        self._loop = asyncio.get_event_loop()
        self._handler = DeviceLogHandler(self)
        self._handler.setFormatter(
            logging.Formatter("%(name)s - %(message)s")
        )

        # Add handler to root logger to capture all logs
        root_logger = logging.getLogger()
        root_logger.addHandler(self._handler)

    def add_sync(self, entry: DeviceLogEntry) -> None:
        """Add a log entry synchronously (called from logging handler)."""
        self._entries.append(entry)

        # Notify SSE subscribers if we have an event loop
        if self._loop and self._subscribers:
            for queue in self._subscribers:
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    pass  # Skip if subscriber queue is full

    async def add(self, message: str, level: str = "info", logger_name: str = "device") -> None:
        """Add a log entry asynchronously."""
        if len(message) > MAX_MESSAGE_LENGTH:
            message = message[:MAX_MESSAGE_LENGTH] + "..."

        entry = DeviceLogEntry(
            timestamp=datetime.now(),
            message=message,
            level=level,
            logger_name=logger_name,
        )

        async with self._lock:
            self._entries.append(entry)

            # Notify SSE subscribers
            for queue in self._subscribers:
                try:
                    queue.put_nowait(entry)
                except asyncio.QueueFull:
                    pass

    async def get_logs(
        self,
        level: str | None = None,
        logger_name: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Get logs with optional filtering.

        Args:
            level: Filter by log level (debug, info, warning, error)
            logger_name: Filter by logger name (prefix match)
            limit: Maximum number of entries to return (newest first)
        """
        async with self._lock:
            entries = list(self._entries)

        # Apply filters
        if level:
            entries = [e for e in entries if e.level == level.lower()]

        if logger_name:
            entries = [e for e in entries if e.logger_name.startswith(logger_name)]

        # Sort by timestamp descending (newest first) and apply limit
        entries = sorted(entries, key=lambda e: e.timestamp, reverse=True)
        if limit:
            entries = entries[:limit]

        return [e.to_dict() for e in entries]

    async def clear(self) -> bool:
        """Clear all logs."""
        async with self._lock:
            self._entries.clear()
        return True

    async def subscribe(self) -> AsyncIterator[DeviceLogEntry]:
        """
        Subscribe to real-time device logs via SSE.

        Usage:
            async for entry in device_log_manager.subscribe():
                yield f"data: {json.dumps(entry.to_dict())}\n\n"
        """
        # Small queue to prevent memory buildup if consumer is slow
        queue: asyncio.Queue[DeviceLogEntry] = asyncio.Queue(maxsize=100)

        async with self._lock:
            self._subscribers.append(queue)

        try:
            while True:
                entry = await queue.get()
                yield entry
        finally:
            async with self._lock:
                try:
                    self._subscribers.remove(queue)
                except ValueError:
                    pass

    async def get_stats(self) -> dict:
        """Get log statistics."""
        async with self._lock:
            entries = list(self._entries)

        level_counts = {}
        logger_counts = {}

        for entry in entries:
            level_counts[entry.level] = level_counts.get(entry.level, 0) + 1
            logger_counts[entry.logger_name] = logger_counts.get(entry.logger_name, 0) + 1

        return {
            "total": len(entries),
            "max_entries": MAX_ENTRIES,
            "by_level": level_counts,
            "by_logger": logger_counts,
        }


# Global device log manager instance
device_log_manager = DeviceLogManager()

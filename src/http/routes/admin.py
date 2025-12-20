"""
Admin routes for device status and system information.
"""

import asyncio
import os
import platform
import subprocess
import time

import psutil
from litestar import Controller, get, post
from litestar.response import Response

from ...streaming import StreamManager


class AdminController(Controller):
    """Admin endpoints for device management."""

    path = "/api"

    @get("/status")
    async def get_status(self, stream_manager: StreamManager) -> dict:
        """Get device and stream status."""
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime_seconds = time.time() - psutil.boot_time()

        return {
            "status": "online",
            "device_id": os.getenv("DEVICE_ID", "unknown"),
            "uptime_seconds": int(uptime_seconds),
            "cpu_percent": cpu_percent,
            "memory": {
                "total_mb": memory.total // (1024 * 1024),
                "used_mb": memory.used // (1024 * 1024),
                "percent": memory.percent,
            },
            "disk": {
                "total_gb": disk.total // (1024 * 1024 * 1024),
                "used_gb": disk.used // (1024 * 1024 * 1024),
                "percent": disk.percent,
            },
        }

    @get("/system")
    async def get_system(self) -> dict:
        """Get system information."""
        # Get temperature (Raspberry Pi)
        temperature = None
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temperature = int(f.read()) / 1000.0
        except Exception:
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries:
                            temperature = entries[0].current
                            break
            except Exception:
                pass

        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        uptime_seconds = time.time() - psutil.boot_time()

        return {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "device_id": os.getenv("DEVICE_ID", "unknown"),
            "temperature": temperature,
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "uptime_seconds": int(uptime_seconds),
        }

    @get("/device-info")
    async def get_device_info(self, stream_manager: StreamManager) -> dict:
        """Get device info from backend state."""
        state = stream_manager._last_state or {}
        device_info = state.get("device", {})
        complex_info = state.get("complex", {})

        # Get YouTube broadcasts info
        broadcasts = state.get("broadcasts", [])
        active_youtube_streams = []
        for broadcast in broadcasts:
            broadcast_id = broadcast.get("id")
            is_running = (
                broadcast_id in stream_manager._youtube_streams
                and stream_manager._youtube_streams[broadcast_id].poll() is None
            )
            active_youtube_streams.append({
                "id": broadcast_id,
                "camera_id": broadcast.get("camera_id"),
                "camera_name": broadcast.get("camera_name"),
                "is_running": is_running,
            })

        return {
            "device_id": os.getenv("DEVICE_ID", "unknown"),
            "device_name": device_info.get("name", os.getenv("DEVICE_ID", "unknown")),
            "complex_name": complex_info.get("name"),
            "complex_id": complex_info.get("id"),
            "youtube_broadcasts": active_youtube_streams,
        }

    @post("/restart")
    async def restart_device(self) -> dict:
        """Restart the device."""
        asyncio.create_task(self._delayed_restart())
        return {
            "status": "restarting",
            "message": "Device will restart in 5 seconds",
        }

    async def _delayed_restart(self) -> None:
        """Restart the device after a delay."""
        await asyncio.sleep(5)
        subprocess.run(["sudo", "reboot"])

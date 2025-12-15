"""
HTTP server for device remote access.
Provides REST API for device management and monitoring.
"""

import asyncio
import logging
from aiohttp import web

logger = logging.getLogger(__name__)


class DeviceHTTPServer:
    """HTTP server for device remote management."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        self.app.router.add_get("/", self.handle_root)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/api/status", self.handle_status)
        self.app.router.add_get("/api/cameras", self.handle_cameras)
        self.app.router.add_get("/api/system", self.handle_system)
        self.app.router.add_post("/api/restart", self.handle_restart)

    async def start(self) -> None:
        """Start the HTTP server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()

        logger.info(f"HTTP server started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self.runner:
            await self.runner.cleanup()
            logger.info("HTTP server stopped")

    # Route handlers

    async def handle_root(self, request: web.Request) -> web.Response:
        """Root endpoint - device info."""
        return web.json_response({
            "name": "BeachVar Device",
            "version": "1.0.0",
            "endpoints": [
                "/health",
                "/api/status",
                "/api/cameras",
                "/api/system",
                "/api/restart",
            ],
        })

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "ok"})

    async def handle_status(self, request: web.Request) -> web.Response:
        """Device status endpoint."""
        import os
        import psutil

        # Get system info
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        # Get uptime
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])

        return web.json_response({
            "status": "online",
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
        })

    async def handle_cameras(self, request: web.Request) -> web.Response:
        """List available cameras."""
        import subprocess

        cameras = []

        # Detect USB cameras
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse v4l2-ctl output
                lines = result.stdout.strip().split("\n")
                current_name = None
                for line in lines:
                    if not line.startswith("\t"):
                        current_name = line.strip().rstrip(":")
                    elif line.strip().startswith("/dev/video"):
                        device = line.strip()
                        cameras.append({
                            "id": device.replace("/dev/", ""),
                            "name": current_name or device,
                            "path": device,
                            "status": "available",
                        })
        except Exception as e:
            logger.warning(f"Failed to detect cameras: {e}")

        return web.json_response({"cameras": cameras})

    async def handle_system(self, request: web.Request) -> web.Response:
        """System information endpoint."""
        import platform
        import os

        # Get temperature (Raspberry Pi)
        temperature = None
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temperature = int(f.read()) / 1000.0
        except Exception:
            pass

        return web.json_response({
            "hostname": platform.node(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "temperature_celsius": temperature,
            "environment": {
                "DEVICE_ID": os.getenv("DEVICE_ID", "unknown"),
            },
        })

    async def handle_restart(self, request: web.Request) -> web.Response:
        """Restart device endpoint."""
        import subprocess

        logger.warning("Restart requested via HTTP API")

        # Schedule restart
        asyncio.create_task(self._delayed_restart())

        return web.json_response({
            "status": "restarting",
            "message": "Device will restart in 5 seconds",
        })

    async def _delayed_restart(self) -> None:
        """Restart the device after a delay."""
        await asyncio.sleep(5)
        import subprocess
        subprocess.run(["sudo", "reboot"])

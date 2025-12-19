"""
HTTP server for device remote access.
Provides REST API for device management and monitoring.

HLS Security Note:
- HLS files are served publicly without authentication
- Security is handled by Cloudflare Snippet which validates HMAC signatures
- Backend generates signed URLs, Cloudflare validates them at the edge
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web

from ..streaming import StreamManager

logger = logging.getLogger(__name__)

# Type hint for GPIO handler (optional dependency)
GPIOButtonHandler = None

# Static files directory
STATIC_DIR = Path(__file__).parent / "static"

# HLS output directory
HLS_DIR = Path("/tmp/hls")


class DeviceHTTPServer:
    """HTTP server for device remote management."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        stream_manager: Optional[StreamManager] = None,
        device_token: Optional[str] = None,
        gpio_handler: Optional["GPIOButtonHandler"] = None,
    ):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.device_id = os.getenv("DEVICE_ID", "unknown")
        self.device_token = device_token or os.getenv("DEVICE_TOKEN", "")
        self.backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        self.stream_manager = stream_manager
        self.gpio_handler = gpio_handler
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        # Health check (public - for monitoring)
        self.app.router.add_get("/health", self.handle_health)

        # Admin routes (protected by Cloudflare: /admin/*)
        self.app.router.add_get("/admin/status", self.handle_status)
        self.app.router.add_get("/admin/system", self.handle_system)
        self.app.router.add_get("/admin/device-info", self.handle_device_info)
        self.app.router.add_post("/admin/restart", self.handle_restart)

        # Registered cameras management (protected by Cloudflare)
        self.app.router.add_get("/admin/registered-cameras", self.handle_registered_cameras)
        self.app.router.add_post("/admin/registered-cameras", self.handle_create_camera)
        self.app.router.add_delete("/admin/registered-cameras/{camera_id}", self.handle_delete_camera)

        # Stream management (protected by Cloudflare)
        self.app.router.add_get("/admin/streams", self.handle_streams_list)
        self.app.router.add_post("/admin/streams/{camera_id}/start", self.handle_stream_start)
        self.app.router.add_post("/admin/streams/{camera_id}/stop", self.handle_stream_stop)
        self.app.router.add_get("/admin/streams/{camera_id}/status", self.handle_stream_status)

        # GPIO Buttons management (protected by Cloudflare)
        self.app.router.add_get("/admin/buttons", self.handle_buttons_list)
        self.app.router.add_post("/admin/buttons", self.handle_button_create)
        self.app.router.add_patch("/admin/buttons/{button_id}", self.handle_button_update)
        self.app.router.add_delete("/admin/buttons/{button_id}", self.handle_button_delete)

        # HLS streaming (public - security handled by Cloudflare Snippet)
        self.app.router.add_get("/hls/{camera_id}/{filename}", self.handle_hls_file)

        # Static files / frontend (protected by Cloudflare: /admin/*)
        self.app.router.add_get("/admin/", self.handle_index)
        self.app.router.add_get("/admin", self.handle_index)
        if STATIC_DIR.exists():
            self.app.router.add_static("/admin/static/", path=STATIC_DIR, name="static")

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

    async def handle_index(self, request: web.Request) -> web.Response:
        """Serve the frontend HTML."""
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return web.FileResponse(index_file)
        # Fallback to JSON if no frontend
        return web.json_response({
            "name": "BeachVar Device",
            "version": "1.0.0",
            "device_id": self.device_id,
            "endpoints": {
                "public": [
                    "/health",
                    "/hls/{camera_id}/{filename}",
                ],
                "admin": [
                    "/admin/ (this page)",
                    "/admin/status",
                    "/admin/system",
                    "/admin/restart",
                    "/admin/registered-cameras",
                    "/admin/streams",
                ],
            },
        })

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "ok"})

    async def handle_hls_file(self, request: web.Request) -> web.Response:
        """
        Serve HLS files (m3u8 playlist and .ts segments) with CORS headers.

        Security Note:
        - Files are served publicly without authentication
        - Security is handled by Cloudflare Snippet which validates HMAC signatures
        - Backend generates signed URLs, Cloudflare validates them at the edge
        """
        camera_id = request.match_info.get("camera_id")
        filename = request.match_info.get("filename")

        if not camera_id or not filename:
            return web.Response(status=400, text="Missing camera_id or filename")

        # Security: only allow m3u8 and ts files
        if not (filename.endswith(".m3u8") or filename.endswith(".ts")):
            return web.Response(status=400, text="Invalid file type")

        # Security: prevent path traversal
        if ".." in camera_id or ".." in filename:
            return web.Response(status=400, text="Invalid path")

        file_path = HLS_DIR / camera_id / filename

        if not file_path.exists():
            return web.Response(status=404, text="File not found")

        # Use FileResponse for efficient streaming (sendfile syscall when available)
        try:
            response = web.FileResponse(
                file_path,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                },
            )
            return response
        except Exception as e:
            logger.error(f"Error reading HLS file {file_path}: {e}")
            return web.Response(status=500, text="Error reading file")

    async def handle_status(self, request: web.Request) -> web.Response:
        """Device status endpoint."""
        import psutil
        import time

        # Get system info
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        # Get uptime (cross-platform)
        uptime_seconds = time.time() - psutil.boot_time()

        return web.json_response({
            "status": "online",
            "device_id": self.device_id,
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


    async def handle_system(self, request: web.Request) -> web.Response:
        """System information endpoint."""
        import platform
        import time
        import psutil

        # Get temperature (Raspberry Pi)
        temperature = None
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temperature = int(f.read()) / 1000.0
        except Exception:
            # Try psutil for other platforms
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        if entries:
                            temperature = entries[0].current
                            break
            except Exception:
                pass

        # Get CPU and memory
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        uptime_seconds = time.time() - psutil.boot_time()

        return web.json_response({
            "hostname": platform.node(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "device_id": self.device_id,
            "temperature": temperature,
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "uptime_seconds": int(uptime_seconds),
        })

    async def handle_device_info(self, request: web.Request) -> web.Response:
        """Device info from backend state."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        # Get cached state from stream manager
        state = self.stream_manager._last_state or {}

        # Extract device info from state
        device_info = state.get("device", {})
        complex_info = state.get("complex", {})

        # Get YouTube broadcasts info
        broadcasts = state.get("broadcasts", [])
        active_youtube_streams = []
        for broadcast in broadcasts:
            broadcast_id = broadcast.get("id")
            is_running = (
                broadcast_id in self.stream_manager._youtube_streams
                and self.stream_manager._youtube_streams[broadcast_id].poll() is None
            )
            active_youtube_streams.append({
                "id": broadcast_id,
                "camera_id": broadcast.get("camera_id"),
                "camera_name": broadcast.get("camera_name"),
                "is_running": is_running,
            })

        return web.json_response({
            "device_id": self.device_id,
            "device_name": device_info.get("name", self.device_id),
            "complex_name": complex_info.get("name"),
            "complex_id": complex_info.get("id"),
            "youtube_broadcasts": active_youtube_streams,
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
        import subprocess
        await asyncio.sleep(5)
        subprocess.run(["sudo", "reboot"])

    # ==================== Registered Cameras Handlers ====================

    async def handle_registered_cameras(self, request: web.Request) -> web.Response:
        """List cameras registered with the backend."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        # Refresh cameras from backend
        cameras = await self.stream_manager.refresh_cameras()

        return web.json_response({
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
                    "stream": {
                        "live_input_id": cam.stream.live_input_id,
                        "playback_hls": cam.stream.playback_hls,
                        "playback_dash": cam.stream.playback_dash,
                    } if cam.stream else None,
                    "is_streaming": cam.id in self.stream_manager.active_streams,
                }
                for cam in cameras
            ],
            "total": len(cameras),
        })

    async def handle_create_camera(self, request: web.Request) -> web.Response:
        """Create a new camera registration."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )

        # Validate required fields
        required = ["name", "rtsp_url", "court_id"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return web.json_response(
                {"error": f"Missing required fields: {', '.join(missing)}"},
                status=400
            )

        camera = await self.stream_manager.create_camera(
            name=data["name"],
            rtsp_url=data["rtsp_url"],
            court_id=data["court_id"],
            position=data.get("position", "other"),
        )

        if not camera:
            return web.json_response(
                {"error": "Failed to create camera"},
                status=500
            )

        return web.json_response({
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
        }, status=201)

    async def handle_delete_camera(self, request: web.Request) -> web.Response:
        """Delete a camera registration."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        camera_id = request.match_info.get("camera_id")
        if not camera_id:
            return web.json_response(
                {"error": "Camera ID required"},
                status=400
            )

        success = await self.stream_manager.delete_camera(camera_id)
        if not success:
            return web.json_response(
                {"error": "Failed to delete camera"},
                status=500
            )

        return web.Response(status=204)

    # ==================== Stream Management Handlers ====================

    async def handle_streams_list(self, request: web.Request) -> web.Response:
        """List all active streams."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        streams = await self.stream_manager.get_all_streams()

        return web.json_response({
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
        })

    async def handle_stream_start(self, request: web.Request) -> web.Response:
        """Start streaming from a camera."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        camera_id = request.match_info.get("camera_id")
        if not camera_id:
            return web.json_response(
                {"error": "Camera ID required"},
                status=400
            )

        stream_info = await self.stream_manager.start_stream(camera_id)
        if not stream_info:
            return web.json_response(
                {"error": "Failed to start stream"},
                status=500
            )

        return web.json_response({
            "id": stream_info.id,
            "status": stream_info.status,
            "started_at": stream_info.started_at,
            "message": "Stream started successfully",
        })

    async def handle_stream_stop(self, request: web.Request) -> web.Response:
        """Stop streaming from a camera."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        camera_id = request.match_info.get("camera_id")
        if not camera_id:
            return web.json_response(
                {"error": "Camera ID required"},
                status=400
            )

        success = await self.stream_manager.stop_stream(camera_id)
        if not success:
            return web.json_response(
                {"error": "No active stream found or failed to stop"},
                status=400
            )

        return web.json_response({
            "message": "Stream stopped successfully",
        })

    async def handle_stream_status(self, request: web.Request) -> web.Response:
        """Get stream status for a camera."""
        if not self.stream_manager:
            return web.json_response(
                {"error": "Stream manager not configured"},
                status=503
            )

        camera_id = request.match_info.get("camera_id")
        if not camera_id:
            return web.json_response(
                {"error": "Camera ID required"},
                status=400
            )

        stream_info = await self.stream_manager.get_stream_status(camera_id)
        if not stream_info:
            return web.json_response({
                "status": "idle",
                "message": "No active stream",
            })

        return web.json_response({
            "id": stream_info.id,
            "status": stream_info.status,
            "started_at": stream_info.started_at,
            "stopped_at": stream_info.stopped_at,
            "duration_seconds": stream_info.duration_seconds,
            "bitrate_kbps": stream_info.bitrate_kbps,
            "viewers_count": stream_info.viewers_count,
            "error_message": stream_info.error_message,
            "is_active": stream_info.is_active,
        })

    # ==================== GPIO Buttons Handlers ====================

    def _get_auth_headers(self) -> dict:
        """Get authentication headers for backend API."""
        import base64
        credentials = f"{self.device_id}:{self.device_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
        }

    async def handle_buttons_list(self, request: web.Request) -> web.Response:
        """List all configured GPIO buttons from backend."""
        if not self.backend_url:
            return web.json_response(
                {"error": "Backend URL not configured"},
                status=503
            )

        url = f"{self.backend_url}/api/device/buttons/"
        headers = self._get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Add GPIO status if handler is available
                        buttons = data.get("buttons", [])
                        if self.gpio_handler:
                            for btn in buttons:
                                gpio_pin = btn.get("gpio_pin")
                                btn["is_monitoring"] = gpio_pin in self.gpio_handler.buttons
                        return web.json_response(data)
                    else:
                        error = await response.text()
                        logger.error(f"Failed to fetch buttons: {response.status} - {error}")
                        return web.json_response(
                            {"error": f"Backend error: {response.status}"},
                            status=response.status
                        )
        except Exception as e:
            logger.error(f"Error fetching buttons: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def handle_button_create(self, request: web.Request) -> web.Response:
        """Create a new GPIO button configuration."""
        if not self.backend_url:
            return web.json_response(
                {"error": "Backend URL not configured"},
                status=503
            )

        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )

        # Validate required fields
        required = ["button_number", "gpio_pin"]
        missing = [f for f in required if f not in data]
        if missing:
            return web.json_response(
                {"error": f"Missing required fields: {', '.join(missing)}"},
                status=400
            )

        url = f"{self.backend_url}/api/device/buttons/create/"
        headers = self._get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as response:
                    result = await response.json()
                    if response.status in (200, 201):
                        # Refresh GPIO handler config
                        if self.gpio_handler:
                            await self.gpio_handler.refresh_config()
                        return web.json_response(result, status=201)
                    else:
                        return web.json_response(result, status=response.status)
        except Exception as e:
            logger.error(f"Error creating button: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def handle_button_update(self, request: web.Request) -> web.Response:
        """Update a GPIO button configuration."""
        if not self.backend_url:
            return web.json_response(
                {"error": "Backend URL not configured"},
                status=503
            )

        button_id = request.match_info.get("button_id")
        if not button_id:
            return web.json_response(
                {"error": "Button ID required"},
                status=400
            )

        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )

        url = f"{self.backend_url}/api/device/buttons/{button_id}/"
        headers = self._get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(url, headers=headers, json=data) as response:
                    result = await response.json()
                    if response.status == 200:
                        # Refresh GPIO handler config
                        if self.gpio_handler:
                            await self.gpio_handler.refresh_config()
                        return web.json_response(result)
                    else:
                        return web.json_response(result, status=response.status)
        except Exception as e:
            logger.error(f"Error updating button: {e}")
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def handle_button_delete(self, request: web.Request) -> web.Response:
        """Delete a GPIO button configuration."""
        if not self.backend_url:
            return web.json_response(
                {"error": "Backend URL not configured"},
                status=503
            )

        button_id = request.match_info.get("button_id")
        if not button_id:
            return web.json_response(
                {"error": "Button ID required"},
                status=400
            )

        url = f"{self.backend_url}/api/device/buttons/{button_id}/"
        headers = self._get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=headers) as response:
                    if response.status == 204:
                        # Refresh GPIO handler config
                        if self.gpio_handler:
                            await self.gpio_handler.refresh_config()
                        return web.Response(status=204)
                    else:
                        try:
                            result = await response.json()
                            return web.json_response(result, status=response.status)
                        except Exception:
                            return web.json_response(
                                {"error": f"Backend error: {response.status}"},
                                status=response.status
                            )

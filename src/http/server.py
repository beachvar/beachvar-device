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
import subprocess
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web

from ..streaming import StreamManager

logger = logging.getLogger(__name__)

# ttyd process for web terminal
_ttyd_process: Optional[subprocess.Popen] = None

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
    ):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.device_id = os.getenv("DEVICE_ID", "unknown")
        self.device_token = device_token or os.getenv("DEVICE_TOKEN", "")
        self.stream_manager = stream_manager
        self._ttyd_port = 7682  # Port for ttyd web terminal
        self._ttyd_process: Optional[subprocess.Popen] = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        # Health check (public - for monitoring)
        self.app.router.add_get("/health", self.handle_health)

        # Admin routes (protected by Cloudflare: /admin/*)
        self.app.router.add_get("/admin/status", self.handle_status)
        self.app.router.add_get("/admin/system", self.handle_system)
        self.app.router.add_post("/admin/restart", self.handle_restart)
        self.app.router.add_get("/admin/terminal-config", self.handle_terminal_config)

        # Registered cameras management (protected by Cloudflare)
        self.app.router.add_get("/admin/registered-cameras", self.handle_registered_cameras)
        self.app.router.add_post("/admin/registered-cameras", self.handle_create_camera)
        self.app.router.add_delete("/admin/registered-cameras/{camera_id}", self.handle_delete_camera)

        # Stream management (protected by Cloudflare)
        self.app.router.add_get("/admin/streams", self.handle_streams_list)
        self.app.router.add_post("/admin/streams/{camera_id}/start", self.handle_stream_start)
        self.app.router.add_post("/admin/streams/{camera_id}/stop", self.handle_stream_stop)
        self.app.router.add_get("/admin/streams/{camera_id}/status", self.handle_stream_status)

        # HLS streaming (public - security handled by Cloudflare Snippet)
        self.app.router.add_get("/hls/{camera_id}/{filename}", self.handle_hls_file)

        # Terminal proxy (protected by Cloudflare: /admin/*)
        # Proxy all /admin/terminal/* requests to ttyd
        self.app.router.add_route("*", "/admin/terminal/{path:.*}", self.handle_terminal_proxy)
        self.app.router.add_route("*", "/admin/terminal", self.handle_terminal_proxy)

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

        # Start ttyd web terminal for SSH access to host
        await self._start_ttyd()

    async def stop(self) -> None:
        """Stop the HTTP server."""
        # Stop ttyd
        await self._stop_ttyd()

        if self.runner:
            await self.runner.cleanup()
            logger.info("HTTP server stopped")

    async def _start_ttyd(self) -> None:
        """Start ttyd web terminal for SSH access to host."""
        # Get host SSH config from environment
        ssh_host = os.getenv("SSH_HOST", "host.docker.internal")
        ssh_port = os.getenv("SSH_PORT", "22")
        ssh_user = os.getenv("SSH_USER", "pi")
        ssh_key_path = os.getenv("SSH_KEY_PATH", "")

        # Check if ttyd is available
        ttyd_path = "/usr/local/bin/ttyd"
        if not os.path.exists(ttyd_path):
            logger.warning("ttyd not found, web terminal disabled")
            return

        try:
            # Start ttyd with SSH to host
            # -p: port, -t: terminal options
            # --base-path: for cloudflared routing to /terminal/
            cmd = [
                ttyd_path,
                "-p", str(self._ttyd_port),
                "--base-path", "/terminal",
                "-t", "fontSize=14",
                "-t", "fontFamily=monospace",
                "-t", "theme={'background': '#1a1a2e'}",
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ]

            # Add SSH key if configured
            if ssh_key_path and os.path.exists(ssh_key_path):
                cmd.extend(["-i", ssh_key_path])
                logger.info(f"Using SSH key: {ssh_key_path}")

            cmd.extend(["-p", ssh_port, f"{ssh_user}@{ssh_host}"])

            logger.info(f"Starting ttyd with command: {' '.join(cmd)}")

            self._ttyd_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Give ttyd a moment to start and check if it's running
            await asyncio.sleep(0.5)
            if self._ttyd_process.poll() is not None:
                stdout, stderr = self._ttyd_process.communicate()
                logger.error(f"ttyd failed to start. stdout: {stdout.decode()}, stderr: {stderr.decode()}")
                self._ttyd_process = None
                return

            logger.info(f"ttyd web terminal started on port {self._ttyd_port} (SSH to {ssh_user}@{ssh_host}:{ssh_port})")

        except Exception as e:
            logger.error(f"Failed to start ttyd: {e}")

    async def _stop_ttyd(self) -> None:
        """Stop ttyd web terminal."""
        if self._ttyd_process:
            try:
                self._ttyd_process.terminate()
                self._ttyd_process.wait(timeout=5)
                logger.info("ttyd web terminal stopped")
            except Exception as e:
                logger.warning(f"Error stopping ttyd: {e}")
                try:
                    self._ttyd_process.kill()
                except Exception:
                    pass
            self._ttyd_process = None

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
                    "/admin/cameras",
                    "/admin/cameras/scan",
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

    async def handle_terminal_config(self, request: web.Request) -> web.Response:
        """Get terminal configuration for web UI."""
        # Check if ttyd is running
        ttyd_running = self._ttyd_process is not None and self._ttyd_process.poll() is None

        ssh_host = os.getenv("SSH_HOST", "host.docker.internal")
        ssh_user = os.getenv("SSH_USER", "pi")

        return web.json_response({
            "enabled": ttyd_running,
            "port": self._ttyd_port,
            "ssh_host": ssh_host,
            "ssh_user": ssh_user,
        })

    async def handle_terminal_proxy(self, request: web.Request) -> web.StreamResponse:
        """Proxy requests to ttyd web terminal."""
        # Check if ttyd is running
        if not self._ttyd_process or self._ttyd_process.poll() is not None:
            logger.warning("ttyd process is not running")
            return web.Response(status=503, text="Terminal not available - ttyd not running")

        # Get the path after /admin/terminal
        # ttyd uses window.location.pathname to construct URLs, so it expects to be at root
        path = request.match_info.get("path", "")

        # Build URL - proxy to ttyd at root
        url_path = f"/{path}" if path else "/"

        # Include query string if present
        if request.query_string:
            url_path = f"{url_path}?{request.query_string}"

        ttyd_url = f"http://127.0.0.1:{self._ttyd_port}{url_path}"
        logger.debug(f"Terminal proxy: {request.path} -> {ttyd_url}")

        # Check if this is a WebSocket upgrade request
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_websocket(request, ttyd_url)

        # Regular HTTP proxy
        return await self._proxy_http(request, ttyd_url)

    async def _proxy_http(self, request: web.Request, target_url: str) -> web.Response:
        """Proxy regular HTTP requests to ttyd."""
        try:
            async with aiohttp.ClientSession() as session:
                # Forward the request
                async with session.request(
                    method=request.method,
                    url=target_url,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() not in ('host', 'content-length')},
                    data=await request.read() if request.body_exists else None,
                    allow_redirects=False,
                ) as resp:
                    # Build response
                    body = await resp.read()
                    content_type = resp.headers.get('Content-Type', '')

                    # Note: ttyd uses window.location.pathname to construct WebSocket URL
                    # So accessing /admin/terminal/ will correctly use /admin/terminal/ws
                    # No URL rewriting needed - just proxy the requests correctly

                    response = web.Response(
                        status=resp.status,
                        body=body,
                    )

                    # Copy headers (except hop-by-hop)
                    hop_by_hop = {'connection', 'keep-alive', 'transfer-encoding',
                                  'te', 'trailer', 'upgrade', 'content-length'}
                    for key, value in resp.headers.items():
                        if key.lower() not in hop_by_hop:
                            response.headers[key] = value

                    return response

        except aiohttp.ClientError as e:
            logger.error(f"Terminal proxy error: {e}")
            return web.Response(status=502, text="Terminal not available")

    async def _proxy_websocket(self, request: web.Request, target_url: str) -> web.WebSocketResponse:
        """Proxy WebSocket connections to ttyd."""
        # Convert http:// to ws://
        ws_url = target_url.replace("http://", "ws://")
        logger.info(f"WebSocket proxy: connecting to {ws_url}")

        # Get WebSocket subprotocols from client request (ttyd uses 'tty')
        protocols = request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        protocols = [p.strip() for p in protocols if p.strip()]
        logger.info(f"Client requested protocols: {protocols}")

        # Create WebSocket response for client with the same protocols
        ws_client = web.WebSocketResponse(protocols=tuple(protocols) if protocols else None)
        await ws_client.prepare(request)
        logger.info(f"WebSocket client connection prepared with protocol: {ws_client.ws_protocol}")

        try:
            # Connect to ttyd WebSocket with same protocols
            async with aiohttp.ClientSession() as session:
                logger.info(f"Connecting to ttyd WebSocket at {ws_url}")
                async with session.ws_connect(ws_url, protocols=protocols if protocols else None) as ws_server:
                    logger.info(f"Connected to ttyd WebSocket successfully, protocol: {ws_server.protocol}")
                    # Create tasks for bidirectional forwarding
                    async def forward_to_server():
                        try:
                            logger.info("Starting client->server forwarding loop")
                            async for msg in ws_client:
                                logger.info(f"Client->Server: type={msg.type}, data_len={len(msg.data) if hasattr(msg, 'data') and msg.data else 0}")
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    await ws_server.send_str(msg.data)
                                elif msg.type == aiohttp.WSMsgType.BINARY:
                                    await ws_server.send_bytes(msg.data)
                                elif msg.type == aiohttp.WSMsgType.CLOSE:
                                    logger.info("Client closed WebSocket")
                                    await ws_server.close()
                                    break
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    logger.error(f"Client WebSocket error: {ws_client.exception()}")
                                    break
                            logger.info("Client->server forwarding loop ended")
                        except Exception as e:
                            logger.error(f"Error forwarding to server: {e}", exc_info=True)

                    async def forward_to_client():
                        try:
                            logger.info("Starting server->client forwarding loop")
                            async for msg in ws_server:
                                logger.info(f"Server->Client: type={msg.type}, data_len={len(msg.data) if hasattr(msg, 'data') and msg.data else 0}")
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    await ws_client.send_str(msg.data)
                                elif msg.type == aiohttp.WSMsgType.BINARY:
                                    await ws_client.send_bytes(msg.data)
                                elif msg.type == aiohttp.WSMsgType.CLOSE:
                                    logger.info("Server closed WebSocket")
                                    await ws_client.close()
                                    break
                                elif msg.type == aiohttp.WSMsgType.ERROR:
                                    logger.error(f"Server WebSocket error: {ws_server.exception()}")
                                    break
                            logger.info("Server->client forwarding loop ended")
                        except Exception as e:
                            logger.error(f"Error forwarding to client: {e}", exc_info=True)

                    # Run both directions concurrently
                    await asyncio.gather(
                        forward_to_server(),
                        forward_to_client(),
                        return_exceptions=True,
                    )

        except aiohttp.ClientError as e:
            logger.error(f"WebSocket proxy connection error: {e}")
        except Exception as e:
            logger.error(f"WebSocket proxy error: {e}", exc_info=True)

        return ws_client

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

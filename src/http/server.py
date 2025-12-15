"""
HTTP server for device remote access.
Provides REST API for device management and monitoring.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from aiohttp import web

from ..streaming import StreamManager

logger = logging.getLogger(__name__)

# Static files directory
STATIC_DIR = Path(__file__).parent / "static"


class DeviceHTTPServer:
    """HTTP server for device remote management."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        stream_manager: Optional[StreamManager] = None,
    ):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.device_id = os.getenv("DEVICE_ID", "unknown")
        self.stream_manager = stream_manager
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        # API routes
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/api/status", self.handle_status)
        self.app.router.add_get("/api/cameras", self.handle_cameras)
        self.app.router.add_post("/api/cameras/scan", self.handle_cameras_scan)
        self.app.router.add_get("/api/system", self.handle_system)
        self.app.router.add_post("/api/restart", self.handle_restart)

        # Registered cameras management (from backend)
        self.app.router.add_get("/api/registered-cameras", self.handle_registered_cameras)
        self.app.router.add_post("/api/registered-cameras", self.handle_create_camera)
        self.app.router.add_delete("/api/registered-cameras/{camera_id}", self.handle_delete_camera)

        # Stream management
        self.app.router.add_get("/api/streams", self.handle_streams_list)
        self.app.router.add_post("/api/streams/{camera_id}/start", self.handle_stream_start)
        self.app.router.add_post("/api/streams/{camera_id}/stop", self.handle_stream_stop)
        self.app.router.add_get("/api/streams/{camera_id}/status", self.handle_stream_status)

        # Static files (frontend)
        self.app.router.add_get("/", self.handle_index)
        if STATIC_DIR.exists():
            self.app.router.add_static("/static/", path=STATIC_DIR, name="static")

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

    async def handle_cameras_scan(self, request: web.Request) -> web.Response:
        """Perform detailed camera scan (network + local)."""
        import subprocess
        import re
        import socket
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        cameras = []
        scan_log = []

        scan_log.append("Iniciando varredura de cameras...")

        # Get network prefix from:
        # 1. Query parameter (?network=192.168.68)
        # 2. Environment variable (SCAN_NETWORK)
        # 3. Auto-detect from local IP
        network_prefix = request.query.get("network")

        if not network_prefix:
            network_prefix = os.getenv("SCAN_NETWORK")

        if not network_prefix:
            local_ip = self._get_local_ip()
            network_prefix = ".".join(local_ip.split(".")[:3]) if local_ip else None
            scan_log.append(f"IP local detectado: {local_ip}")
        else:
            scan_log.append(f"Usando rede configurada: {network_prefix}")

        # Method 1: Network scan for IP cameras
        if network_prefix:
            scan_log.append(f"Varrendo rede {network_prefix}.0/24...")

            # Common camera ports
            camera_ports = [
                (554, "RTSP"),      # RTSP streaming
                (80, "HTTP"),       # Web interface
                (8080, "HTTP-Alt"), # Alt web interface
                (443, "HTTPS"),     # Secure web
                (8554, "RTSP-Alt"), # Alt RTSP
                (37777, "Dahua"),   # Dahua cameras
                (34567, "XMEye"),   # XMEye/Generic Chinese
                (5000, "ONVIF"),    # ONVIF discovery
            ]

            # Scan network in parallel
            discovered_hosts = await self._scan_network(network_prefix, camera_ports, scan_log)

            for host_info in discovered_hosts:
                ip = host_info["ip"]
                open_ports = host_info["ports"]

                # Determine camera type based on ports
                cam_type = "IP Camera"
                rtsp_url = None

                if 554 in open_ports or 8554 in open_ports:
                    rtsp_port = 554 if 554 in open_ports else 8554
                    rtsp_url = f"rtsp://{ip}:{rtsp_port}/stream"
                    cam_type = "RTSP Camera"
                if 37777 in open_ports:
                    cam_type = "Dahua Camera"
                    rtsp_url = f"rtsp://{ip}:554/cam/realmonitor?channel=1&subtype=0"
                if 34567 in open_ports:
                    cam_type = "XMEye Camera"

                # Try to get more info via HTTP
                camera_name = await self._get_camera_name(ip, open_ports)

                cam_info = {
                    "id": f"ip-{ip.replace('.', '-')}",
                    "name": camera_name or f"Camera {ip}",
                    "ip": ip,
                    "type": cam_type,
                    "status": "available",
                    "ports": open_ports,
                    "rtsp_url": rtsp_url,
                    "web_url": f"http://{ip}" if 80 in open_ports else None,
                }
                cameras.append(cam_info)
                scan_log.append(f"Encontrada: {cam_info['name']} ({ip}) - {cam_type}")

        # Method 2: v4l2-ctl (Linux Video4Linux) for local USB cameras
        scan_log.append("Verificando dispositivos V4L2 locais...")
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                current_name = None
                for line in lines:
                    if not line.startswith("\t"):
                        current_name = line.strip().rstrip(":")
                    elif line.strip().startswith("/dev/video"):
                        device = line.strip()
                        cam_info = {
                            "id": device.replace("/dev/", ""),
                            "name": current_name or device,
                            "path": device,
                            "status": "available",
                            "type": "v4l2",
                            "formats": [],
                            "resolutions": [],
                        }

                        # Get supported formats
                        try:
                            fmt_result = subprocess.run(
                                ["v4l2-ctl", "-d", device, "--list-formats-ext"],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if fmt_result.returncode == 0:
                                # Parse formats
                                format_matches = re.findall(
                                    r"Pixel Format: '(\w+)'",
                                    fmt_result.stdout
                                )
                                cam_info["formats"] = list(set(format_matches))

                                # Parse resolutions
                                res_matches = re.findall(
                                    r"Size: Discrete (\d+x\d+)",
                                    fmt_result.stdout
                                )
                                cam_info["resolutions"] = list(set(res_matches))
                        except Exception:
                            pass

                        # Get device capabilities
                        try:
                            cap_result = subprocess.run(
                                ["v4l2-ctl", "-d", device, "--all"],
                                capture_output=True,
                                text=True,
                                timeout=5,
                            )
                            if cap_result.returncode == 0:
                                # Check if it's a capture device
                                if "Video Capture" in cap_result.stdout:
                                    cam_info["capabilities"] = ["capture"]
                                if "Video Output" in cap_result.stdout:
                                    cam_info.setdefault("capabilities", []).append("output")

                                # Get driver info
                                driver_match = re.search(
                                    r"Driver name\s*:\s*(\S+)",
                                    cap_result.stdout
                                )
                                if driver_match:
                                    cam_info["driver"] = driver_match.group(1)

                                # Get card info
                                card_match = re.search(
                                    r"Card type\s*:\s*(.+)",
                                    cap_result.stdout
                                )
                                if card_match:
                                    cam_info["card"] = card_match.group(1).strip()

                                # Get bus info
                                bus_match = re.search(
                                    r"Bus info\s*:\s*(.+)",
                                    cap_result.stdout
                                )
                                if bus_match:
                                    cam_info["bus"] = bus_match.group(1).strip()
                        except Exception:
                            pass

                        cameras.append(cam_info)
                        scan_log.append(f"Encontrada: {cam_info['name']} ({device})")
            else:
                scan_log.append("v4l2-ctl nao encontrou dispositivos")
        except FileNotFoundError:
            scan_log.append("v4l2-ctl nao instalado")
        except subprocess.TimeoutExpired:
            scan_log.append("v4l2-ctl timeout")
        except Exception as e:
            scan_log.append(f"Erro v4l2-ctl: {str(e)}")

        # Method 2: Check /dev/video* directly
        scan_log.append("Verificando /dev/video*...")
        try:
            video_devices = list(Path("/dev").glob("video*"))
            for dev_path in video_devices:
                device = str(dev_path)
                # Check if already found
                if not any(c["path"] == device for c in cameras):
                    cameras.append({
                        "id": dev_path.name,
                        "name": f"Video Device ({dev_path.name})",
                        "path": device,
                        "status": "available",
                        "type": "unknown",
                    })
                    scan_log.append(f"Encontrado dispositivo: {device}")
        except Exception as e:
            scan_log.append(f"Erro ao verificar /dev: {str(e)}")

        # Method 3: USB devices (lsusb)
        scan_log.append("Verificando dispositivos USB...")
        usb_cameras = []
        try:
            result = subprocess.run(
                ["lsusb"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in ["camera", "webcam", "video", "cam"]):
                        # Parse lsusb output: Bus XXX Device YYY: ID XXXX:YYYY Name
                        match = re.search(
                            r"Bus (\d+) Device (\d+): ID ([0-9a-f:]+) (.+)",
                            line,
                            re.IGNORECASE
                        )
                        if match:
                            usb_cameras.append({
                                "bus": match.group(1),
                                "device": match.group(2),
                                "usb_id": match.group(3),
                                "name": match.group(4).strip(),
                            })
                            scan_log.append(f"USB Camera: {match.group(4).strip()}")
        except FileNotFoundError:
            scan_log.append("lsusb nao instalado")
        except Exception as e:
            scan_log.append(f"Erro lsusb: {str(e)}")

        # Filter out metadata devices (usually odd-numbered)
        # On Linux, video0 is capture, video1 is metadata
        capture_cameras = []
        for cam in cameras:
            path = cam.get("path", "")
            if "video" in path:
                try:
                    num = int(re.search(r"video(\d+)", path).group(1))
                    # Keep only even-numbered or check capabilities
                    caps = cam.get("capabilities", [])
                    if "capture" in caps or num % 2 == 0:
                        capture_cameras.append(cam)
                except Exception:
                    capture_cameras.append(cam)
            else:
                capture_cameras.append(cam)

        scan_log.append(f"Varredura concluida: {len(capture_cameras)} camera(s) encontrada(s)")

        return web.json_response({
            "cameras": capture_cameras,
            "usb_devices": usb_cameras,
            "scan_log": scan_log,
            "total_found": len(capture_cameras),
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
        import subprocess
        subprocess.run(["sudo", "reboot"])

    def _get_local_ip(self) -> str | None:
        """Get local IP address."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    async def _scan_network(
        self,
        network_prefix: str,
        camera_ports: list[tuple[int, str]],
        scan_log: list[str],
    ) -> list[dict]:
        """Scan network for IP cameras."""
        import socket
        import asyncio

        discovered = []
        port_numbers = [p[0] for p in camera_ports]

        async def check_host(ip: str) -> dict | None:
            """Check if host has any camera ports open."""
            open_ports = []
            for port in port_numbers:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, port),
                        timeout=0.5
                    )
                    writer.close()
                    await writer.wait_closed()
                    open_ports.append(port)
                except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                    pass

            if open_ports:
                return {"ip": ip, "ports": open_ports}
            return None

        # Scan all IPs in parallel (1-254)
        tasks = [check_host(f"{network_prefix}.{i}") for i in range(1, 255)]

        # Process in batches to avoid too many connections
        batch_size = 50
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            results = await asyncio.gather(*batch, return_exceptions=True)
            for result in results:
                if result and isinstance(result, dict):
                    discovered.append(result)

        scan_log.append(f"Encontrados {len(discovered)} hosts com portas de camera")
        return discovered

    async def _get_camera_name(self, ip: str, open_ports: list[int]) -> str | None:
        """Try to get camera name via HTTP."""
        import aiohttp

        if 80 not in open_ports and 8080 not in open_ports:
            return None

        port = 80 if 80 in open_ports else 8080

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=2)
            ) as session:
                # Try common endpoints
                endpoints = [
                    f"http://{ip}:{port}/",
                    f"http://{ip}:{port}/cgi-bin/magicBox.cgi?action=getDeviceType",
                ]
                for url in endpoints:
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                # Try to extract name from response
                                if "deviceType" in text:
                                    # Dahua format
                                    import re
                                    match = re.search(r"deviceType=(.+)", text)
                                    if match:
                                        return match.group(1).strip()
                                # Check title tag
                                import re
                                title_match = re.search(
                                    r"<title>([^<]+)</title>",
                                    text,
                                    re.IGNORECASE
                                )
                                if title_match:
                                    title = title_match.group(1).strip()
                                    if title and len(title) < 50:
                                        return title
                    except Exception:
                        pass
        except Exception:
            pass

        return None

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

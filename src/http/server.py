"""
HTTP server for device remote access.
Provides REST API for device management and monitoring.
"""

import asyncio
import logging
import os
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)

# Static files directory
STATIC_DIR = Path(__file__).parent / "static"


class DeviceHTTPServer:
    """HTTP server for device remote management."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.device_id = os.getenv("DEVICE_ID", "unknown")
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
        """Perform detailed camera scan."""
        import subprocess
        import re

        cameras = []
        scan_log = []

        scan_log.append("Iniciando varredura de cameras...")

        # Method 1: v4l2-ctl (Linux Video4Linux)
        scan_log.append("Verificando dispositivos V4L2...")
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

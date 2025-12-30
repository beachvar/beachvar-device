"""
Stream manager for camera management and live streaming.
Handles communication with backend API and FFmpeg processes.
"""

import asyncio
import logging
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from urllib.parse import quote, urlparse

import aiohttp

from .camera import CameraConfig
from .logs import log_manager
from .device_logs import device_log_manager
from ..sentry import traced, TracingContext, set_camera_context, clear_camera_context, capture_exception

logger = logging.getLogger(__name__)

# HLS output directory for local streaming
HLS_OUTPUT_DIR = "/tmp/hls"


@dataclass
class StreamProcess:
    """Active FFmpeg stream process."""

    camera_id: str
    camera_name: str
    process: subprocess.Popen
    live_stream_id: Optional[str] = None
    started_at: Optional[str] = None
    started_timestamp: float = field(default_factory=time.time)
    log_reader_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.process.poll() is None

    @property
    def uptime_seconds(self) -> float:
        """Get stream uptime in seconds."""
        return time.time() - self.started_timestamp


class StreamManager:
    """
    Manages camera registration and live streaming.
    Communicates with backend API and manages FFmpeg processes.
    """

    def __init__(
        self,
        backend_url: str,
        device_token: str,
        device_id: str,
        device_public_url: Optional[str] = None,
        on_connection_change: Optional[Callable] = None,
    ):
        """
        Initialize the stream manager.

        Args:
            backend_url: Backend API URL (e.g., https://api.beachvar.com)
            device_token: Device authentication token
            device_id: Device UUID for Basic Auth
            device_public_url: Public URL for this device (e.g., https://device-id.devices.beachvar.com)
            on_connection_change: Callback for connection status changes
        """
        self.backend_url = backend_url.rstrip("/")
        self.device_token = device_token
        self.device_id = device_id
        self.device_public_url = device_public_url.rstrip("/") if device_public_url else None
        self.on_connection_change = on_connection_change

        # Cached cameras
        self._cameras: dict[str, CameraConfig] = {}

        # Active stream processes
        self._streams: dict[str, StreamProcess] = {}

        # Monitor task
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def cameras(self) -> dict[str, CameraConfig]:
        """Get dict of registered cameras by ID."""
        return self._cameras

    def get_camera_list(self) -> list[CameraConfig]:
        """Get list of registered cameras."""
        return list(self._cameras.values())

    def remove_camera(self, camera_id: str) -> bool:
        """Remove a camera from the cache."""
        if camera_id in self._cameras:
            del self._cameras[camera_id]
            logger.info(f"Removed camera {camera_id} from cache")
            return True
        return False

    @property
    def active_streams(self) -> list[str]:
        """Get list of camera IDs with active streams."""
        return [cam_id for cam_id, stream in self._streams.items() if stream.is_running]

    def _get_headers(self) -> dict:
        """Get HTTP headers for API requests using Basic Auth."""
        import base64
        credentials = f"{self.device_id}:{self.device_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Basic {encoded}",
        }

    async def start(self) -> None:
        """Start the stream manager."""
        self._running = True

        # Load cameras from backend
        await self.refresh_cameras()

        # Start monitor task
        self._monitor_task = asyncio.create_task(self._monitor_streams())

        logger.info("Stream manager started")

    async def stop(self) -> None:
        """Stop the stream manager and all streams."""
        self._running = False

        # Stop monitor task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Stop all active streams
        for camera_id in list(self._streams.keys()):
            await self.stop_stream(camera_id)

        logger.info("Stream manager stopped")

    # ==================== Device State (Consolidated Endpoint) ====================

    # Cache for last fetched state
    _last_state: dict | None = None

    async def fetch_device_state(self) -> dict | None:
        """
        Fetch consolidated device state from backend.
        Returns cameras, broadcasts, config, and sponsors in a single call.
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/state/"
                logger.debug(f"Fetching device state from {url}")
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.debug(f"Device state fetched: {len(data.get('cameras', []))} cameras, {len(data.get('broadcasts', []))} broadcasts")
                        self._last_state = data
                        return data
                    else:
                        error = await resp.text()
                        logger.error(f"Failed to fetch device state: {resp.status} - {error}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching device state: {e}")
            return None

    @traced(op="sync", name="sync_device_state")
    async def sync_device_state(self) -> bool:
        """
        Sync device state from backend in a single call.
        Updates cameras and syncs YouTube broadcasts (starts new ones, stops removed ones).

        Returns:
            True if sync was successful
        """
        with TracingContext(op="http", description="fetch_device_state") as span:
            state = await self.fetch_device_state()
            span.set_data("success", state is not None)
            if state:
                span.set_data("cameras_count", len(state.get("cameras", [])))
                span.set_data("broadcasts_count", len(state.get("broadcasts", [])))

        if state is None:
            return False

        # Update cameras
        with TracingContext(op="task", description="process_cameras") as span:
            camera_list = state.get("cameras", [])
            cameras = [CameraConfig.from_dict(c) for c in camera_list]
            new_camera_ids = {c.id for c in cameras}
            old_camera_ids = set(self._cameras.keys())

            # Find cameras that were deleted from backend
            deleted_camera_ids = old_camera_ids - new_camera_ids
            span.set_data("deleted_count", len(deleted_camera_ids))

            for camera_id in deleted_camera_ids:
                camera = self._cameras.get(camera_id)
                camera_name = camera.name if camera else camera_id
                logger.info(f"Camera {camera_name} ({camera_id}) removed from backend, cleaning up...")
                await self._cleanup_deleted_camera(camera_id)

            # Update camera cache with new data
            self._cameras = {c.id: c for c in cameras}

        # Sync YouTube broadcasts
        with TracingContext(op="task", description="sync_youtube_broadcasts"):
            await self._sync_youtube_broadcasts(state.get("broadcasts", []))

        return True

    async def _sync_youtube_broadcasts(self, backend_broadcasts: list[dict]) -> None:
        """
        Sync YouTube broadcasts with backend state.
        - Start new broadcasts that aren't running locally
        - Stop broadcasts that were removed from backend
        - Clean up failed broadcast tracking when backend removes them

        Args:
            backend_broadcasts: List of active broadcasts from backend
        """
        # Get set of broadcast IDs from backend
        backend_broadcast_ids = {b["id"] for b in backend_broadcasts}

        # Get set of locally running broadcast IDs
        local_broadcast_ids = set(self._youtube_streams.keys())

        # Find broadcasts to start (in backend but not running locally)
        broadcasts_to_start = backend_broadcast_ids - local_broadcast_ids

        # Find broadcasts to stop (running locally but not in backend anymore)
        broadcasts_to_stop = local_broadcast_ids - backend_broadcast_ids

        # Clean up "stopping" broadcasts that are no longer in backend
        # (backend has processed the stop request)
        stale_stopping = self._youtube_stopping_broadcasts - backend_broadcast_ids
        if stale_stopping:
            logger.info(f"Cleaning up {len(stale_stopping)} stopped broadcast(s) no longer in backend")
            self._youtube_stopping_broadcasts -= stale_stopping

        # Clean up failed broadcasts that are no longer in backend
        # (backend marked them as ERROR, so they're not returned as active)
        stale_failed = self._youtube_failed_broadcasts - backend_broadcast_ids
        if stale_failed:
            logger.info(f"Cleaning up {len(stale_failed)} failed broadcast(s) no longer in backend")
            self._youtube_failed_broadcasts -= stale_failed
            # Also clean retry tracking for stale broadcasts
            for broadcast_id in stale_failed:
                self._youtube_retry_counts.pop(broadcast_id, None)
                self._youtube_pending_retries.discard(broadcast_id)

        # Stop broadcasts that were removed from backend
        for broadcast_id in broadcasts_to_stop:
            logger.info(f"Stopping YouTube broadcast {broadcast_id} (removed from backend)")
            process = self._youtube_streams.get(broadcast_id)
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                except Exception as e:
                    logger.error(f"Error stopping YouTube broadcast {broadcast_id}: {e}")

            self._youtube_streams.pop(broadcast_id, None)
            self._youtube_last_heartbeat.pop(broadcast_id, None)
            # Clean retry tracking for stopped broadcasts
            self._youtube_retry_counts.pop(broadcast_id, None)
            self._youtube_pending_retries.discard(broadcast_id)

        # Start new broadcasts
        for broadcast_data in backend_broadcasts:
            broadcast_id = broadcast_data["id"]
            if broadcast_id not in broadcasts_to_start:
                continue

            # Skip broadcasts that are being stopped (prevents race condition restart)
            if broadcast_id in self._youtube_stopping_broadcasts:
                logger.debug(
                    f"Skipping broadcast {broadcast_id} - marked as stopping, "
                    "waiting for backend to process stop request"
                )
                continue

            # Skip broadcasts that recently failed (prevents restart loop)
            if broadcast_id in self._youtube_failed_broadcasts:
                logger.debug(
                    f"Skipping broadcast {broadcast_id} - marked as failed, "
                    "waiting for backend to acknowledge error status"
                )
                continue

            camera_id = broadcast_data["camera_id"]
            camera_name = broadcast_data.get("camera_name", camera_id)
            rtmp_url = broadcast_data.get("rtmp_url", "")
            stream_key = broadcast_data.get("stream_key", "")

            if not rtmp_url or not stream_key:
                logger.warning(f"Broadcast {broadcast_id} missing RTMP URL or stream key")
                continue

            # Check if HLS stream is running for this camera
            if camera_id not in self._streams or not self._streams[camera_id].is_running:
                logger.debug(f"HLS stream not ready for {camera_name}, will retry on next sync")
                continue

            logger.info(f"Starting new YouTube broadcast for {camera_name}: {broadcast_id}")
            await self.start_youtube_stream(
                camera_id=camera_id,
                broadcast_id=broadcast_id,
                rtmp_url=rtmp_url,
                stream_key=stream_key,
            )

    # ==================== Camera Management ====================

    async def refresh_cameras(self) -> list[CameraConfig]:
        """Fetch cameras from backend and update cache."""
        # Use sync_device_state which also syncs broadcasts
        success = await self.sync_device_state()
        if not success:
            # Fallback to legacy endpoint
            return await self._refresh_cameras_legacy()

        cameras = list(self._cameras.values())
        logger.info(f"Loaded {len(cameras)} cameras from backend:")
        for cam in cameras:
            has_stream_config = "YES" if cam.has_stream_config else "NO"
            logger.info(f"  - {cam.name} (ID: {cam.id[:8]}...) stream={has_stream_config}")
        return cameras

    async def _refresh_cameras_legacy(self) -> list[CameraConfig]:
        """Fetch cameras from legacy endpoint (fallback)."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/"
                logger.info(f"Fetching cameras from legacy endpoint {url}")
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        camera_list = data.get("cameras", [])
                        cameras = [CameraConfig.from_dict(c) for c in camera_list]
                        self._cameras = {c.id: c for c in cameras}
                        logger.info(f"Loaded {len(cameras)} cameras from backend:")
                        for cam in cameras:
                            has_stream_config = "YES" if cam.has_stream_config else "NO"
                            logger.info(f"  - {cam.name} (ID: {cam.id[:8]}...) stream={has_stream_config}")
                        return cameras
                    else:
                        error = await resp.text()
                        logger.error(f"Failed to fetch cameras: {resp.status} - {error}")
                        return []
        except Exception as e:
            logger.error(f"Error fetching cameras: {e}")
            return []

    async def create_camera(
        self,
        name: str,
        rtsp_url: str,
        court_id: str,
    ) -> Optional[CameraConfig]:
        """
        Register a new camera with the backend.

        Args:
            name: Camera name
            rtsp_url: RTSP URL for the camera
            court_id: Court UUID to associate camera with

        Returns:
            Created CameraConfig or None on error
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/create/"
                payload = {
                    "name": name,
                    "rtsp_url": rtsp_url,
                    "court_id": court_id,
                }

                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=payload
                ) as resp:
                    if resp.status == 201:
                        data = await resp.json()
                        # Backend returns {"success": true, "camera": {...}}
                        camera_data = data.get("camera", data)
                        camera = CameraConfig.from_dict(camera_data)
                        self._cameras[camera.id] = camera
                        logger.info(f"Created camera: {camera.name} ({camera.id})")
                        return camera
                    else:
                        error = await resp.text()
                        logger.error(f"Failed to create camera: {resp.status} - {error}")
                        return None
        except Exception as e:
            logger.error(f"Error creating camera: {e}")
            return None

    async def get_camera(self, camera_id: str) -> Optional[CameraConfig]:
        """Get camera details from backend."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/"
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        camera = CameraConfig.from_dict(data)
                        self._cameras[camera.id] = camera
                        return camera
                    elif resp.status == 404:
                        # Camera was deleted from backend - clean up locally
                        logger.warning(f"Camera {camera_id} not found on backend (deleted?)")
                        await self._cleanup_deleted_camera(camera_id)
                        return None
                    else:
                        logger.error(f"Failed to get camera {camera_id}: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Error getting camera {camera_id}: {e}")
            return None

    async def update_camera(
        self, camera_id: str, update_data: dict
    ) -> Optional[CameraConfig]:
        """Update a camera on backend."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/"
                async with session.put(
                    url, headers=self._get_headers(), json=update_data
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Backend returns {"success": true, "camera": {...}}
                        # Update local cache with new values
                        camera_data = data.get("camera", {})
                        camera = self._cameras.get(camera_id)
                        if camera:
                            # Update fields that may have changed
                            if "name" in camera_data:
                                camera.name = camera_data["name"]
                            if "rtsp_url" in camera_data:
                                camera.rtsp_url = camera_data["rtsp_url"]
                            logger.info(f"Updated camera: {camera.name} ({camera.id})")
                        return camera
                    else:
                        error = await resp.text()
                        logger.error(f"Failed to update camera {camera_id}: {resp.status} - {error}")
                        return None
        except Exception as e:
            logger.error(f"Error updating camera {camera_id}: {e}")
            return None

    async def delete_camera(self, camera_id: str) -> bool:
        """Delete a camera from backend."""
        # First stop any active stream
        if camera_id in self._streams:
            await self.stop_stream(camera_id)

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/"
                async with session.delete(url, headers=self._get_headers()) as resp:
                    if resp.status == 204:
                        self._cameras.pop(camera_id, None)
                        logger.info(f"Deleted camera: {camera_id}")
                        return True
                    else:
                        logger.error(f"Failed to delete camera {camera_id}: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Error deleting camera {camera_id}: {e}")
            return False

    # ==================== Stream Management ====================

    @traced(op="stream", name="start_stream")
    async def start_stream(self, camera_id: str) -> bool:
        """
        Start streaming from a camera via local HLS.

        Args:
            camera_id: Camera UUID to start streaming

        Returns:
            True if started successfully, False on error
        """
        # Get camera config first for logging
        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        # Set Sentry context for this camera
        set_camera_context(camera_id, camera_name)

        logger.info(f"=== Starting stream for {camera_name} ({camera_id}) ===")

        try:
            # Check if already streaming
            if camera_id in self._streams and self._streams[camera_id].is_running:
                logger.warning(f"Camera {camera_name} is already streaming")
                return False

            # Get camera config
            with TracingContext(op="http", description="fetch_camera") as span:
                if not camera:
                    camera = await self.get_camera(camera_id)
                span.set_data("camera_found", camera is not None)

            if not camera:
                logger.error(f"Camera {camera_id} not found")
                return False

            if not camera.has_stream_config:
                logger.error(f"Camera {camera.name} has no RTSP URL configured")
                return False

            # Update camera name after fetch
            set_camera_context(camera_id, camera.name)

            # Start FFmpeg process
            with TracingContext(op="subprocess", description="start_ffmpeg") as span:
                logger.info(f"Starting FFmpeg for {camera.name}...")
                process = self._start_ffmpeg(camera)
                span.set_data("pid", process.pid)

            # Initialize log manager for this camera
            await log_manager.init_camera(camera_id, camera.name)
            await log_manager.add_log(
                camera_id, f"FFmpeg started (PID: {process.pid})", "info", camera.name
            )

            # Start log reader task
            log_reader_task = asyncio.create_task(
                self._read_ffmpeg_logs(camera_id, camera.name, process)
            )

            self._streams[camera_id] = StreamProcess(
                camera_id=camera_id,
                camera_name=camera.name,
                process=process,
                log_reader_task=log_reader_task,
            )

            logger.info(f"FFmpeg started for {camera.name} (PID: {process.pid})")

            # Small delay to let FFmpeg initialize
            await asyncio.sleep(0.5)

            # Update backend with connection status
            with TracingContext(op="http", description="update_connection") as span:
                logger.info(f"Updating connection to 'connected' for {camera.name}...")
                status_updated = await self._update_connection(camera_id, is_connected=True)
                span.set_data("success", status_updated)

            if status_updated:
                logger.info(f"=== Stream LIVE for {camera.name} ===")
            else:
                logger.warning(f"=== Stream started but connection update failed for {camera.name} ===")

            if self.on_connection_change:
                self.on_connection_change(camera_id, True)

            return True

        except Exception as e:
            logger.error(f"Failed to start FFmpeg for {camera.name}: {e}")
            capture_exception(e, camera_id=camera_id, camera_name=camera_name)
            await self._update_connection(camera_id, is_connected=False, error_message=str(e))
            return False
        finally:
            clear_camera_context()

    def get_active_streams(self) -> list["StreamProcess"]:
        """Get list of all active streams."""
        return list(self._streams.values())

    async def stop_stream(self, camera_id: str) -> bool:
        """
        Stop streaming from a camera.

        Args:
            camera_id: Camera UUID to stop streaming

        Returns:
            True if stopped successfully
        """
        stream = self._streams.get(camera_id)
        if not stream:
            logger.warning(f"No active stream for camera {camera_id}")
            return False

        # Stop FFmpeg process gracefully (same pattern as stream.py)
        try:
            # Cancel log reader task first
            if stream.log_reader_task and not stream.log_reader_task.done():
                stream.log_reader_task.cancel()
                try:
                    await asyncio.wait_for(stream.log_reader_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            if stream.is_running:
                # Use terminate() first (SIGTERM) - cleaner than SIGINT
                stream.process.terminate()

                # Wait up to 3 seconds for graceful shutdown
                try:
                    stream.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Force kill if not responding
                    try:
                        stream.process.kill()
                        stream.process.wait()
                    except Exception:
                        pass

            # Log the stop event
            await log_manager.add_log(
                camera_id, "Stream stopped by user", "info", stream.camera_name
            )

            logger.info(f"Stopped stream for camera {camera_id}")

        except Exception as e:
            logger.error(f"Error stopping FFmpeg for camera {camera_id}: {e}")

        # Remove from active streams
        del self._streams[camera_id]

        # Clean up HLS files
        self.cleanup_hls_files(camera_id)

        # Notify backend that camera is disconnected
        await self._update_connection(camera_id, is_connected=False)

        if self.on_connection_change:
            self.on_connection_change(camera_id, False)

        return True

    # ==================== FFmpeg Management ====================

    def _check_rtsp_connectivity(self, rtsp_url: str, timeout: float = 5.0) -> bool:
        """
        Check if RTSP camera is reachable via TCP connection.

        This is a quick check to avoid starting FFmpeg if the camera is offline.
        Does NOT validate RTSP stream, just TCP connectivity.

        Args:
            rtsp_url: RTSP URL (rtsp://user:pass@host:port/path)
            timeout: Connection timeout in seconds

        Returns:
            True if camera is reachable, False otherwise
        """
        try:
            parsed = urlparse(rtsp_url)
            host = parsed.hostname
            port = parsed.port or 554  # Default RTSP port

            if not host:
                logger.warning(f"Could not parse host from RTSP URL")
                return False

            # Try TCP connection
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                return True
            else:
                logger.debug(f"RTSP camera at {host}:{port} not reachable (error code: {result})")
                return False

        except socket.timeout:
            logger.debug(f"RTSP camera connection timed out")
            return False
        except socket.gaierror as e:
            logger.debug(f"RTSP camera DNS resolution failed: {e}")
            return False
        except Exception as e:
            logger.debug(f"RTSP connectivity check failed: {e}")
            return False

    def _encode_rtsp_url(self, rtsp_url: str) -> str:
        """
        URL encode password in RTSP URL to handle special characters.

        Handles URLs like: rtsp://user:pass@word!@host:port/path
        where password contains @ or other special characters.

        If password is already URL-encoded (contains %XX patterns), it's
        first decoded to avoid double-encoding.
        """
        from urllib.parse import unquote

        # Parse the URL - match last @ before host:port or host/path
        # This handles passwords containing @ like "Hestia!@#$"
        match = re.match(
            r'^(rtsp://)?([^:]+):(.+)@(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}[:/].*)$',
            rtsp_url
        )

        if match:
            scheme = match.group(1) or "rtsp://"
            user = match.group(2)
            password = match.group(3)
            rest = match.group(4)

            # Decode first to handle already-encoded passwords (avoid double-encoding)
            # e.g., %21 -> ! -> %21 (instead of %21 -> %2521)
            decoded_password = unquote(password)

            # URL encode the password
            encoded_password = quote(decoded_password, safe='')

            return f"{scheme}{user}:{encoded_password}@{rest}"

        return rtsp_url

    def _start_ffmpeg(self, camera: CameraConfig) -> subprocess.Popen:
        """
        Start FFmpeg process to stream from RTSP to local HLS.

        Args:
            camera: Camera configuration with stream details

        Returns:
            FFmpeg subprocess
        """
        rtsp_url = self._encode_rtsp_url(camera.rtsp_url)
        cmd = self._build_hls_ffmpeg_cmd(camera, rtsp_url)

        logger.info(f"Starting FFmpeg for camera {camera.name}: {rtsp_url} -> HLS")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        # Start FFmpeg as subprocess with stderr captured for logging
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        return process

    async def _read_ffmpeg_logs(
        self,
        camera_id: str,
        camera_name: str,
        process: subprocess.Popen,
        process_type: str = "hls",
    ) -> None:
        """
        Read FFmpeg stderr output asynchronously and store in log manager.

        This runs in background and captures all FFmpeg output for debugging.

        Args:
            camera_id: Camera UUID
            camera_name: Camera display name
            process: FFmpeg subprocess
            process_type: Type of FFmpeg process (hls, youtube)
        """
        loop = asyncio.get_event_loop()
        logger_name = f"ffmpeg.{process_type}.{camera_name}"

        try:
            while process.poll() is None:
                # Read stderr in thread to avoid blocking
                line = await loop.run_in_executor(
                    None,
                    lambda: process.stderr.readline() if process.stderr else b""
                )

                if not line:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        # Determine log level based on content
                        level = "error" if any(
                            x in text.lower() for x in ["error", "fatal", "failed"]
                        ) else "warning" if "warning" in text.lower() else "info"

                        # Send to camera-specific logs
                        await log_manager.add_log(camera_id, text, level, camera_name)

                        # Also send to device-wide logs
                        await device_log_manager.add(
                            message=f"[{camera_name}] {text}",
                            level=level,
                            logger_name=logger_name,
                        )
                except Exception:
                    pass

            # Process ended - read any remaining output
            if process.stderr:
                remaining = process.stderr.read()
                if remaining:
                    for line in remaining.decode("utf-8", errors="ignore").strip().split("\n"):
                        if line:
                            await log_manager.add_log(camera_id, line, "info", camera_name)
                            await device_log_manager.add(
                                message=f"[{camera_name}] {line}",
                                level="info",
                                logger_name=logger_name,
                            )

            # Log exit code
            exit_code = process.returncode
            exit_msg = f"FFmpeg exited with code {exit_code}"
            exit_level = "error" if exit_code != 0 else "info"

            await log_manager.add_log(camera_id, exit_msg, exit_level, camera_name)
            await device_log_manager.add(
                message=f"[{camera_name}] {exit_msg}",
                level=exit_level,
                logger_name=logger_name,
            )

        except asyncio.CancelledError:
            await log_manager.add_log(camera_id, "Log reader cancelled", "info", camera_name)
        except Exception as e:
            logger.error(f"Error reading FFmpeg logs for {camera_name}: {e}")

    def _build_hls_ffmpeg_cmd(self, camera: CameraConfig, rtsp_url: str) -> list[str]:
        """Build FFmpeg command for local HLS output."""
        import os
        import secrets
        import shutil

        # Validate camera ID
        if not camera.id:
            raise ValueError(f"Camera ID is empty for camera: {camera.name}")

        # Create HLS directory for this camera (clean first to remove old segments)
        hls_dir = os.path.join(HLS_OUTPUT_DIR, camera.id)
        logger.info(f"HLS directory for {camera.name}: {hls_dir}")

        if os.path.exists(hls_dir):
            shutil.rmtree(hls_dir)
        os.makedirs(hls_dir, exist_ok=True)

        output_path = os.path.join(hls_dir, "playlist.m3u8")
        logger.info(f"HLS output path: {output_path}")

        # Generate random token for segment filenames (security through obscurity)
        # This makes it harder to guess segment URLs without the playlist
        segment_token = secrets.token_hex(8)  # 16 chars hex
        segment_pattern = os.path.join(hls_dir, f"{segment_token}_%03d.ts")
        logger.info(f"HLS segment pattern: {segment_pattern}")

        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",

            # Input options - optimized for RTSP with low latency
            "-rtsp_transport", "tcp",
            "-timeout", "10000000",  # 10 seconds timeout for socket I/O operations (microseconds)
            "-fflags", "+genpts+discardcorrupt+nobuffer",  # nobuffer reduces input latency
            "-flags", "low_delay",  # Low latency mode
            "-use_wallclock_as_timestamps", "1",
            "-i", rtsp_url,

            # Map video and audio
            "-map", "0:v:0",
            "-map", "0:a:0?",

            # Video: stream copy (no re-encoding) - uses ~0% CPU
            # Most IP cameras already output H.264, so just pass through
            "-c:v", "copy",

            # Audio: transcode to AAC (HLS requires AAC, cameras may use other codecs)
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-af", "aresample=async=1000",  # Sync audio timestamps, fix gaps/drift from camera

            # HLS output options for live streaming with low latency
            "-f", "hls",
            "-hls_time", "2",            # 2-second segments for better stability
            "-hls_list_size", "120",     # Keep last 120 segments in playlist (4 min DVR window with 2s segments)
            "-hls_flags", "delete_segments",  # Delete old segment files
            "-hls_segment_filename", segment_pattern,
            output_path,
        ]

    def get_hls_url(self, camera_id: str, signed: bool = True) -> Optional[str]:
        """
        Get the public HLS URL for a camera.

        Args:
            camera_id: Camera UUID
            signed: If True, include HMAC signature (12h expiry)

        Returns:
            HLS URL (signed or unsigned based on parameter)
        """
        if not self.device_public_url:
            return None

        base_url = f"{self.device_public_url}/hls/{camera_id}/playlist.m3u8"

        if signed and self.device_token:
            # Generate signed URL with 12h expiry
            expires = int(time.time()) + (12 * 3600)
            message = f"{camera_id}:{expires}"
            import hashlib
            import hmac as hmac_mod
            signature = hmac_mod.new(
                self.device_token.encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()
            return f"{base_url}?expires={expires}&sig={signature}"

        return base_url

    def cleanup_hls_files(self, camera_id: str) -> None:
        """Clean up HLS files for a camera."""
        import os
        import shutil

        hls_dir = os.path.join(HLS_OUTPUT_DIR, camera_id)
        if os.path.exists(hls_dir):
            try:
                shutil.rmtree(hls_dir)
                logger.info(f"Cleaned up HLS files for camera {camera_id}")
            except Exception as e:
                logger.warning(f"Failed to clean up HLS files for camera {camera_id}: {e}")

    # ==================== Backend Communication ====================

    async def _update_connection(
        self,
        camera_id: str,
        is_connected: bool,
        error_message: Optional[str] = None,
        retries: int = 3,
    ) -> bool:
        """Update camera connection status on backend with retry logic."""
        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/connection/"
                    payload = {"is_connected": is_connected}
                    if error_message:
                        payload["error"] = error_message

                    async with session.post(
                        url,
                        headers=self._get_headers(),
                        json=payload
                    ) as resp:
                        if resp.status == 200:
                            status_str = "connected" if is_connected else "disconnected"
                            logger.info(f"Updated connection for {camera_name} to {status_str}")
                            return True
                        elif resp.status == 404:
                            # Camera was deleted from backend - clean up locally
                            logger.warning(
                                f"Camera {camera_name} ({camera_id}) not found on backend (deleted?). "
                                "Cleaning up local state..."
                            )
                            await self._cleanup_deleted_camera(camera_id)
                            return False
                        else:
                            error = await resp.text()
                            logger.warning(
                                f"Failed to update connection for {camera_name}: "
                                f"{resp.status} - {error} (attempt {attempt + 1}/{retries})"
                            )
                            if attempt < retries - 1:
                                await asyncio.sleep(0.5 * (attempt + 1))  # Backoff
            except Exception as e:
                logger.error(f"Error updating connection for {camera_name}: {e} (attempt {attempt + 1}/{retries})")
                if attempt < retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        logger.error(f"Failed to update connection for {camera_name} after {retries} attempts")
        return False

    async def _cleanup_deleted_camera(self, camera_id: str) -> None:
        """Clean up local state for a camera that was deleted from backend."""
        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        # Stop any active stream for this camera
        if camera_id in self._streams:
            stream = self._streams[camera_id]
            if stream.is_running:
                logger.info(f"Stopping stream for deleted camera {camera_name}")
                try:
                    stream.process.terminate()
                    try:
                        stream.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        stream.process.kill()
                        stream.process.wait()
                except Exception as e:
                    logger.error(f"Error stopping stream for deleted camera {camera_name}: {e}")

            del self._streams[camera_id]

        # Clean up HLS files
        self.cleanup_hls_files(camera_id)

        # Remove from camera cache
        if camera_id in self._cameras:
            del self._cameras[camera_id]
            logger.info(f"Removed deleted camera {camera_name} ({camera_id}) from cache")

    # ==================== Monitoring ====================

    async def _monitor_streams(self) -> None:
        """Monitor active streams and handle failures with auto-restart."""
        # Track retry counts and last attempt time for each camera
        retry_counts: dict[str, int] = {}
        last_retry_time: dict[str, float] = {}  # Track when last retry started
        pending_restarts: set[str] = set()  # Track cameras with pending restart tasks

        # Configuration - INFINITE retries with extended backoff for overnight recovery
        # Phase 1: Quick retries (first 10 attempts) - for transient failures
        quick_retry_max = 10
        quick_retry_base_delay = 3  # 3s, 5s, 7s... max 30s

        # Phase 2: Extended retries (after 10 attempts) - for camera reboots
        extended_retry_delay = 60  # 1 minute between attempts

        # Phase 3: Long-term recovery (after 30+ attempts)
        long_term_retry_delay = 300  # 5 minutes between attempts

        health_check_interval = 30  # Full health check every 30 seconds
        stable_stream_threshold = 120  # Reset retries after 2 minutes of stable stream
        url_refresh_interval = 6 * 3600  # Refresh HLS URLs every 6 hours (half of 12h expiry)
        stream_heartbeat_interval = 10  # Send heartbeat to backend every 10 seconds
        youtube_heartbeat_interval = 30  # Send YouTube heartbeat every 30 seconds
        last_health_check = 0
        last_url_refresh: dict[str, float] = {}  # Track last URL refresh per camera
        last_stream_heartbeat: dict[str, float] = {}  # Track last heartbeat per camera

        while self._running:
            try:
                await asyncio.sleep(1)  # Check every 1 second for fast detection

                # Monitor existing streams for failures
                for camera_id, stream in list(self._streams.items()):
                    if not stream.is_running:
                        # Stream died unexpectedly
                        camera = self._cameras.get(camera_id)
                        camera_name = camera.name if camera else camera_id
                        logger.warning(f"Stream for {camera_name} ({camera_id}) died after {stream.uptime_seconds:.0f}s")

                        # Get return code
                        returncode = stream.process.returncode
                        stderr = ""
                        try:
                            stderr_bytes = stream.process.stderr.read()
                            stderr = stderr_bytes.decode("utf-8", errors="ignore")[-500:]
                        except Exception:
                            pass

                        # Notify backend of error
                        error_msg = f"FFmpeg exited with code {returncode}"
                        if stderr:
                            # Clean up stderr for logging
                            stderr_clean = stderr.strip().split('\n')[-1] if stderr.strip() else ""
                            if stderr_clean:
                                error_msg += f": {stderr_clean}"

                        logger.error(f"FFmpeg error for {camera_name}: {error_msg}")

                        await self._update_connection(
                            camera_id,
                            is_connected=False,
                            error_message=error_msg,
                        )

                        # Remove from active streams
                        del self._streams[camera_id]

                        if self.on_connection_change:
                            self.on_connection_change(camera_id, False)

                        # Skip if restart already pending
                        if camera_id in pending_restarts:
                            logger.debug(f"Restart already pending for {camera_name}, skipping")
                            continue

                        # INFINITE retry with progressive backoff
                        retry_count = retry_counts.get(camera_id, 0)
                        retry_counts[camera_id] = retry_count + 1

                        # Determine delay based on retry phase
                        if retry_count < quick_retry_max:
                            # Phase 1: Quick retries with short backoff
                            delay = min(quick_retry_base_delay + (retry_count * 2), 30)
                            phase = "quick"
                        elif retry_count < 30:
                            # Phase 2: Extended retries (camera reboot scenario)
                            delay = extended_retry_delay
                            phase = "extended"
                        else:
                            # Phase 3: Long-term recovery
                            delay = long_term_retry_delay
                            phase = "long-term"

                        logger.info(
                            f"Will restart stream for {camera_name} in {delay}s "
                            f"(attempt {retry_count + 1}, {phase} phase)"
                        )

                        pending_restarts.add(camera_id)
                        last_retry_time[camera_id] = time.time()
                        asyncio.create_task(
                            self._delayed_restart_with_check(
                                camera_id, delay, pending_restarts, retry_counts
                            )
                        )

                # Reset retry counts for cameras that have been running stably
                for camera_id, stream in list(self._streams.items()):
                    if stream.is_running and camera_id in retry_counts:
                        if stream.uptime_seconds >= stable_stream_threshold:
                            camera = self._cameras.get(camera_id)
                            camera_name = camera.name if camera else camera_id
                            logger.info(f"Stream for {camera_name} stable for {stream.uptime_seconds:.0f}s, resetting retry count")
                            del retry_counts[camera_id]

                # Periodic full health check - ensure all cameras with streams are active
                current_time = time.time()
                if current_time - last_health_check >= health_check_interval:
                    last_health_check = current_time
                    await self._ensure_all_streams_active(retry_counts)

                # Refresh HLS URLs for local streams before they expire
                for camera_id, stream in list(self._streams.items()):
                    if not stream.is_running:
                        continue

                    camera = self._cameras.get(camera_id)
                    if not camera:
                        continue

                    # Check if URL needs refresh (every 6 hours)
                    last_refresh = last_url_refresh.get(camera_id, stream.started_timestamp)
                    if current_time - last_refresh >= url_refresh_interval:
                        logger.info(f"Refreshing HLS URL for {camera.name} (URL expiring soon)")
                        await self._refresh_hls_url(camera_id)
                        last_url_refresh[camera_id] = current_time

                # Send connection heartbeats to backend (every 10 seconds)
                for camera_id, stream in list(self._streams.items()):
                    if not stream.is_running:
                        continue

                    last_hb = last_stream_heartbeat.get(camera_id, 0)
                    if current_time - last_hb >= stream_heartbeat_interval:
                        # Send heartbeat (just update connection to keep it alive)
                        await self._update_connection(camera_id, is_connected=True)
                        last_stream_heartbeat[camera_id] = current_time

                # Monitor YouTube streams for failures
                for broadcast_id, process in list(self._youtube_streams.items()):
                    if process.poll() is not None:
                        # YouTube FFmpeg process died
                        returncode = process.returncode
                        stderr = ""
                        try:
                            stderr_bytes = process.stderr.read()
                            stderr = stderr_bytes.decode("utf-8", errors="ignore")[-500:] if stderr_bytes else ""
                        except Exception:
                            pass

                        error_msg = f"YouTube FFmpeg exited with code {returncode}"
                        if stderr and stderr.strip():
                            stderr_clean = stderr.strip().split('\n')[-1]
                            error_msg += f": {stderr_clean}"

                        # Remove from active YouTube streams
                        del self._youtube_streams[broadcast_id]
                        self._youtube_last_heartbeat.pop(broadcast_id, None)

                        # Clean up log reader task (it will end on its own but cleanup ref)
                        self._youtube_log_tasks.pop(broadcast_id, None)

                        # Get broadcast data for retry
                        broadcast_data = self._get_broadcast_data_from_cache(broadcast_id)
                        camera_id = broadcast_data.get("camera_id", "") if broadcast_data else ""
                        camera_name = broadcast_data.get("camera_name", broadcast_id) if broadcast_data else broadcast_id

                        # Check retry count
                        retry_count = self._youtube_retry_counts.get(broadcast_id, 0)

                        if retry_count < self.YOUTUBE_MAX_RETRIES and broadcast_id not in self._youtube_pending_retries:
                            # Schedule retry
                            self._youtube_retry_counts[broadcast_id] = retry_count + 1
                            self._youtube_pending_retries.add(broadcast_id)

                            logger.warning(
                                f"YouTube stream {broadcast_id} for {camera_name} failed: {error_msg}. "
                                f"Retry {retry_count + 1}/{self.YOUTUBE_MAX_RETRIES} in {self.YOUTUBE_RETRY_DELAY}s"
                            )

                            asyncio.create_task(
                                self._delayed_youtube_retry(
                                    broadcast_id=broadcast_id,
                                    camera_id=camera_id,
                                    camera_name=camera_name,
                                    delay=self.YOUTUBE_RETRY_DELAY,
                                )
                            )
                        else:
                            # Max retries reached, mark as failed
                            logger.error(
                                f"YouTube stream {broadcast_id} for {camera_name} failed after "
                                f"{self.YOUTUBE_MAX_RETRIES} attempts: {error_msg}"
                            )

                            # Mark as failed BEFORE notifying backend (prevents race with sync)
                            self._youtube_failed_broadcasts.add(broadcast_id)

                            # Notify backend
                            await self._update_youtube_broadcast_status(
                                broadcast_id,
                                status="error",
                                error_message=f"{error_msg} (after {self.YOUTUBE_MAX_RETRIES} retries)",
                            )

                            # Clean up retry tracking
                            self._youtube_retry_counts.pop(broadcast_id, None)
                            self._youtube_pending_retries.discard(broadcast_id)

                # Send YouTube heartbeats (every 30 seconds)
                for broadcast_id, process in list(self._youtube_streams.items()):
                    if process.poll() is not None:
                        continue  # Skip dead processes

                    last_hb = self._youtube_last_heartbeat.get(broadcast_id, 0)
                    if current_time - last_hb >= youtube_heartbeat_interval:
                        # Send heartbeat (just update status to keep it alive)
                        await self._update_youtube_broadcast_status(broadcast_id, status="live")
                        self._youtube_last_heartbeat[broadcast_id] = current_time

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in stream monitor: {e}")

    async def _ensure_all_streams_active(self, retry_counts: dict[str, int]) -> None:
        """Ensure all cameras with stream config have active streams."""
        # Sync device state from backend (cameras + broadcasts in single call)
        try:
            await self.sync_device_state()
        except Exception as e:
            logger.error(f"Failed to sync device state during health check: {e}")
            return

        # Count cameras that should be streaming
        cameras_with_stream = [c for c in self._cameras.values() if c.has_stream_config]
        active_streams = [s for s in self._streams.values() if s.is_running]

        logger.info(
            f"Health check: {len(active_streams)}/{len(cameras_with_stream)} cameras streaming"
        )

        # Check each camera
        cameras_started = 0
        for camera_id, camera in self._cameras.items():
            if not camera.has_stream_config:
                continue

            # Check if stream is already active
            if camera_id in self._streams and self._streams[camera_id].is_running:
                continue

            # Camera should be streaming but isn't - start it
            logger.info(f"Health check: {camera.name} not streaming, starting...")

            try:
                result = await self.start_stream(camera_id)
                if result:
                    logger.info(f"Health check: Started stream for {camera.name}")
                    cameras_started += 1
                    # Reset retry count on successful start
                    retry_counts.pop(camera_id, None)
                else:
                    logger.warning(f"Health check: Failed to start stream for {camera.name}")
                    retry_counts[camera_id] = retry_counts.get(camera_id, 0) + 1
            except Exception as e:
                logger.error(f"Health check: Error starting stream for {camera.name}: {e}")
                retry_counts[camera_id] = retry_counts.get(camera_id, 0) + 1

        if cameras_started > 0:
            logger.info(f"Health check: Started {cameras_started} stream(s)")

    async def _refresh_hls_url(self, camera_id: str) -> bool:
        """Refresh the signed HLS URL on the backend (before it expires)."""
        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        try:
            new_url = self.get_hls_url(camera_id)
            if not new_url:
                logger.warning(f"Could not generate new HLS URL for {camera_name}")
                return False

            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/stream/refresh-url/"
                payload = {"local_hls_url": new_url}

                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=payload
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"Refreshed HLS URL for {camera_name}")
                        return True
                    else:
                        error = await resp.text()
                        logger.warning(f"Failed to refresh HLS URL for {camera_name}: {resp.status} - {error}")
                        return False
        except Exception as e:
            logger.error(f"Error refreshing HLS URL for {camera_name}: {e}")
            return False

    async def _delayed_restart_with_check(
        self,
        camera_id: str,
        delay: float,
        pending_restarts: set[str],
        retry_counts: dict[str, int],
    ) -> None:
        """
        Restart a stream after a delay, with RTSP connectivity check.

        This is the enhanced version that:
        1. Checks RTSP connectivity before starting FFmpeg
        2. Removes from pending_restarts when done
        3. Tracks retry counts properly
        """
        try:
            await asyncio.sleep(delay)

            if not self._running:
                return

            # Check if stream is already running (may have been restarted by health check)
            if camera_id in self._streams and self._streams[camera_id].is_running:
                logger.debug(f"Stream for camera {camera_id} is already running, skipping delayed restart")
                retry_counts.pop(camera_id, None)  # Reset on success
                return

            # Refresh camera config in case it changed
            camera = await self.get_camera(camera_id)
            if not camera or not camera.has_stream_config:
                logger.warning(f"Camera {camera_id} no longer has stream configured, skipping restart")
                retry_counts.pop(camera_id, None)
                return

            camera_name = camera.name

            # Check RTSP connectivity before starting FFmpeg
            # This avoids wasting FFmpeg process starts when camera is unreachable
            if not self._check_rtsp_connectivity(camera.rtsp_url):
                logger.warning(f"Camera {camera_name} not reachable, will retry later")
                # Stream will be retried by monitor loop or health check
                return

            logger.info(f"Auto-restarting stream for {camera_name} (RTSP reachable)")
            result = await self.start_stream(camera_id)

            if result:
                logger.info(f"Successfully restarted stream for {camera_name}")
                retry_counts.pop(camera_id, None)  # Reset on success
            else:
                logger.error(f"Failed to restart stream for {camera_name}")
                # retry_counts already incremented, monitor loop will schedule next retry

        finally:
            # Always remove from pending to allow new restart attempts
            pending_restarts.discard(camera_id)

    async def _delayed_restart(self, camera_id: str, delay: float) -> None:
        """Restart a stream after a delay (legacy method, kept for compatibility)."""
        await asyncio.sleep(delay)

        if not self._running:
            return

        # Check if stream is already running (may have been restarted by health check)
        if camera_id in self._streams and self._streams[camera_id].is_running:
            logger.debug(f"Stream for camera {camera_id} is already running, skipping delayed restart")
            return

        # Refresh camera config in case it changed
        camera = await self.get_camera(camera_id)
        if not camera or not camera.has_stream_config:
            logger.warning(f"Camera {camera_id} no longer has stream configured, skipping restart")
            return

        logger.info(f"Auto-restarting stream for camera {camera_id}")
        result = await self.start_stream(camera_id)
        if result:
            logger.info(f"Successfully restarted stream for camera {camera_id}")
        else:
            logger.error(f"Failed to restart stream for camera {camera_id}")

    # ==================== YouTube Live Streaming ====================

    # Active YouTube stream processes: {broadcast_id: subprocess.Popen}
    _youtube_streams: dict[str, subprocess.Popen] = {}

    # Last heartbeat timestamp for each YouTube broadcast
    _youtube_last_heartbeat: dict[str, float] = {}

    # Broadcasts that failed and should not be auto-restarted
    # This prevents the sync loop from restarting broadcasts that died with errors
    _youtube_failed_broadcasts: set[str] = set()

    # Broadcasts that are being stopped (prevents sync from restarting them)
    # This prevents race condition where sync sees broadcast as active before backend updates
    _youtube_stopping_broadcasts: set[str] = set()

    # YouTube retry tracking: {broadcast_id: retry_count}
    # Used to retry failed broadcasts up to max attempts before marking as failed
    _youtube_retry_counts: dict[str, int] = {}

    # YouTube pending retries: set of broadcast_ids currently waiting to retry
    _youtube_pending_retries: set[str] = set()

    # YouTube retry configuration
    YOUTUBE_MAX_RETRIES = 5
    YOUTUBE_RETRY_DELAY = 5  # seconds

    # YouTube log reader tasks: {broadcast_id: asyncio.Task}
    _youtube_log_tasks: dict[str, asyncio.Task] = {}

    async def start_youtube_stream(
        self,
        camera_id: str,
        broadcast_id: str,
        rtmp_url: str,
        stream_key: str,
    ) -> bool:
        """
        Start streaming from a camera's HLS stream to YouTube.

        This connects to the local HLS playlist (already being generated by the
        primary stream) and re-streams it to YouTube's RTMP endpoint.

        Args:
            camera_id: Camera UUID
            broadcast_id: YouTube broadcast record UUID
            rtmp_url: YouTube RTMP URL (e.g., rtmp://a.rtmp.youtube.com/live2)
            stream_key: YouTube stream key

        Returns:
            True if started successfully
        """
        import os

        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        logger.info(f"=== Starting YouTube stream for {camera_name} (broadcast: {broadcast_id}) ===")

        # Check if already streaming this broadcast
        if broadcast_id in self._youtube_streams:
            if self._youtube_streams[broadcast_id].poll() is None:
                logger.warning(f"YouTube stream for broadcast {broadcast_id} is already running")
                return False

        # Check if camera is streaming locally (HLS)
        hls_playlist = os.path.join(HLS_OUTPUT_DIR, camera_id, "playlist.m3u8")

        if not os.path.exists(hls_playlist):
            logger.error(f"HLS playlist not found for camera {camera_id}: {hls_playlist}")
            logger.info("Camera must be streaming locally (HLS mode) before starting YouTube stream")
            return False

        # Build FFmpeg command to re-stream HLS to YouTube RTMP
        full_rtmp_url = f"{rtmp_url}/{stream_key}"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",

            # Input from HLS - use live_start_index to start from current position
            "-live_start_index", "-1",
            "-i", hls_playlist,

            # Copy video (no re-encoding needed)
            "-c:v", "copy",

            # Re-encode audio to fix timestamp issues and normalize for YouTube
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-af", "aresample=async=1000",  # Sync audio timestamps, fix gaps/drift

            # FLV output for RTMP
            "-f", "flv",
            full_rtmp_url,
        ]

        logger.info(f"Starting YouTube FFmpeg for {camera_name}: HLS -> YouTube")
        logger.debug(f"YouTube FFmpeg command: {' '.join(cmd[:6])}... -> {rtmp_url}/[HIDDEN]")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            self._youtube_streams[broadcast_id] = process

            # Mark as recently started so heartbeat loop doesn't send immediately
            self._youtube_last_heartbeat[broadcast_id] = time.time()

            # Remove from failed set if this is a retry
            self._youtube_failed_broadcasts.discard(broadcast_id)

            # Start log reader task for YouTube FFmpeg
            log_task = asyncio.create_task(
                self._read_ffmpeg_logs(
                    camera_id=camera_id,
                    camera_name=camera_name,
                    process=process,
                    process_type="youtube",
                )
            )
            self._youtube_log_tasks[broadcast_id] = log_task

            logger.info(f"YouTube stream started for {camera_name} (PID: {process.pid})")

            # Notify backend about successful start
            await self._update_youtube_broadcast_status(
                broadcast_id,
                status="live",
                ffmpeg_pid=process.pid,
            )

            return True

        except Exception as e:
            logger.error(f"Failed to start YouTube stream for {camera_name}: {e}")
            await self._update_youtube_broadcast_status(
                broadcast_id,
                status="error",
                error_message=str(e),
            )
            return False

    async def stop_youtube_stream(self, camera_id: str, broadcast_id: str) -> bool:
        """
        Stop streaming to YouTube.

        Args:
            camera_id: Camera UUID
            broadcast_id: YouTube broadcast record UUID

        Returns:
            True if stopped successfully
        """
        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        logger.info(f"=== Stopping YouTube stream for {camera_name} (broadcast: {broadcast_id}) ===")

        # Mark as stopping IMMEDIATELY to prevent sync from restarting it
        # This prevents race condition where sync sees broadcast as active before backend updates
        self._youtube_stopping_broadcasts.add(broadcast_id)

        process = self._youtube_streams.get(broadcast_id)
        if not process:
            logger.warning(f"No active YouTube stream for broadcast {broadcast_id}")
            # Still mark as stopping in case sync is about to start it
            return False

        try:
            if process.poll() is None:  # Still running
                process.terminate()

                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

            logger.info(f"YouTube stream stopped for {camera_name}")

        except Exception as e:
            logger.error(f"Error stopping YouTube stream for {camera_name}: {e}")

        # Cancel log reader task
        log_task = self._youtube_log_tasks.pop(broadcast_id, None)
        if log_task and not log_task.done():
            log_task.cancel()
            try:
                await log_task
            except asyncio.CancelledError:
                pass

        # Remove from active streams
        self._youtube_streams.pop(broadcast_id, None)
        self._youtube_last_heartbeat.pop(broadcast_id, None)

        # Notify backend
        await self._update_youtube_broadcast_status(
            broadcast_id,
            status="complete",
        )

        # Keep in stopping set for a while to handle any pending syncs
        # Will be cleaned up when sync sees it's no longer in backend
        logger.debug(f"Broadcast {broadcast_id} marked as stopping, will be cleaned on next sync")

        return True

    async def _update_youtube_broadcast_status(
        self,
        broadcast_id: str,
        status: str,
        ffmpeg_pid: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        Update YouTube broadcast status on backend.

        Args:
            broadcast_id: UUID of the broadcast record
            status: New status (live, error, complete)
            ffmpeg_pid: PID of FFmpeg process
            error_message: Error message if status is error

        Returns:
            True if updated successfully
        """
        try:
            url = f"{self.backend_url}/api/v1/device/youtube/broadcasts/{broadcast_id}/status/"

            payload = {"status": status}
            if ffmpeg_pid:
                payload["ffmpeg_pid"] = ffmpeg_pid
            if error_message:
                payload["error_message"] = error_message

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=payload,
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"Updated YouTube broadcast {broadcast_id} status to {status}")
                        return True
                    else:
                        logger.warning(f"Failed to update YouTube broadcast status: {resp.status}")
                        return False

        except Exception as e:
            logger.error(f"Error updating YouTube broadcast status: {e}")
            return False

    def _get_broadcast_data_from_cache(self, broadcast_id: str) -> Optional[dict]:
        """
        Get broadcast data from cached device state.

        Args:
            broadcast_id: YouTube broadcast record UUID

        Returns:
            Dict with broadcast data or None if not found
        """
        if not self._last_state:
            return None

        broadcasts = self._last_state.get("broadcasts", [])
        for broadcast in broadcasts:
            if broadcast.get("id") == broadcast_id:
                return broadcast

        return None

    async def _delayed_youtube_retry(
        self,
        broadcast_id: str,
        camera_id: str,
        camera_name: str,
        delay: float,
    ) -> None:
        """
        Retry a YouTube stream after a delay.

        Args:
            broadcast_id: YouTube broadcast record UUID
            camera_id: Camera UUID
            camera_name: Camera name for logging
            delay: Delay in seconds before retry
        """
        try:
            await asyncio.sleep(delay)

            # Remove from pending
            self._youtube_pending_retries.discard(broadcast_id)

            # Check if broadcast is still in backend (not removed)
            broadcast_data = self._get_broadcast_data_from_cache(broadcast_id)
            if not broadcast_data:
                # Broadcast was removed from backend, refresh state and check again
                await self.sync_device_state()
                broadcast_data = self._get_broadcast_data_from_cache(broadcast_id)

            if not broadcast_data:
                logger.info(f"YouTube broadcast {broadcast_id} no longer in backend, skipping retry")
                self._youtube_retry_counts.pop(broadcast_id, None)
                return

            # Check if already running (maybe started by sync)
            if broadcast_id in self._youtube_streams:
                if self._youtube_streams[broadcast_id].poll() is None:
                    logger.info(f"YouTube broadcast {broadcast_id} already running, skipping retry")
                    return

            # Check if marked as failed (max retries reached)
            if broadcast_id in self._youtube_failed_broadcasts:
                logger.debug(f"YouTube broadcast {broadcast_id} marked as failed, skipping retry")
                return

            rtmp_url = broadcast_data.get("rtmp_url", "")
            stream_key = broadcast_data.get("stream_key", "")

            if not rtmp_url or not stream_key:
                logger.warning(f"Cannot retry YouTube broadcast {broadcast_id}: missing RTMP URL or stream key")
                return

            retry_count = self._youtube_retry_counts.get(broadcast_id, 0)
            logger.info(f"Retrying YouTube stream for {camera_name} (attempt {retry_count + 1}/{self.YOUTUBE_MAX_RETRIES})")

            success = await self.start_youtube_stream(
                camera_id=camera_id,
                broadcast_id=broadcast_id,
                rtmp_url=rtmp_url,
                stream_key=stream_key,
            )

            if success:
                logger.info(f"YouTube stream retry successful for {camera_name}")
                # Reset retry count on success
                self._youtube_retry_counts.pop(broadcast_id, None)

        except asyncio.CancelledError:
            self._youtube_pending_retries.discard(broadcast_id)
            raise
        except Exception as e:
            logger.error(f"Error in delayed YouTube retry for {broadcast_id}: {e}")
            self._youtube_pending_retries.discard(broadcast_id)

    def get_youtube_stream_status(self, broadcast_id: str) -> Optional[dict]:
        """
        Get the status of a YouTube stream.

        Args:
            broadcast_id: YouTube broadcast record UUID

        Returns:
            Dict with status info or None if not found
        """
        process = self._youtube_streams.get(broadcast_id)
        if not process:
            return None

        is_running = process.poll() is None

        return {
            "broadcast_id": broadcast_id,
            "pid": process.pid,
            "is_running": is_running,
            "return_code": process.returncode if not is_running else None,
        }

    async def recover_youtube_broadcasts(self, max_retries: int = 5, retry_delay: float = 3.0) -> None:
        """
        Recover active YouTube broadcasts after device restart/deploy.

        Uses sync_device_state to fetch and start broadcasts. Retries up to
        max_retries times, waiting for HLS streams to become available.

        Args:
            max_retries: Maximum number of retry attempts (default: 5)
            retry_delay: Seconds to wait between retries (default: 3.0)
        """
        logger.info("Recovering YouTube broadcasts...")

        for attempt in range(1, max_retries + 1):
            try:
                # Sync state - this will start any broadcasts that have HLS streams ready
                await self.sync_device_state()

                # Check if there are any broadcasts pending (in backend but not running locally)
                if self._last_state:
                    backend_broadcasts = self._last_state.get("broadcasts", [])
                    backend_broadcast_ids = {b["id"] for b in backend_broadcasts}
                    local_broadcast_ids = set(self._youtube_streams.keys())
                    pending = backend_broadcast_ids - local_broadcast_ids

                    if not pending:
                        if backend_broadcasts:
                            logger.info(f"All {len(backend_broadcasts)} YouTube broadcast(s) recovered")
                        else:
                            logger.info("No active YouTube broadcasts to recover")
                        return

                    # Some broadcasts still pending (HLS not ready yet)
                    logger.info(f"Recovery attempt {attempt}/{max_retries}: {len(pending)} broadcast(s) waiting for HLS streams")

                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)

            except Exception as e:
                logger.error(f"Error recovering YouTube broadcasts (attempt {attempt}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)

        # After all retries, mark remaining broadcasts as error
        if self._last_state:
            backend_broadcasts = self._last_state.get("broadcasts", [])
            local_broadcast_ids = set(self._youtube_streams.keys())

            for broadcast in backend_broadcasts:
                broadcast_id = broadcast["id"]
                if broadcast_id not in local_broadcast_ids:
                    camera_name = broadcast.get("camera_name", broadcast["camera_id"])
                    logger.error(f"Failed to recover YouTube broadcast for {camera_name} after {max_retries} attempts")
                    await self._update_youtube_broadcast_status(
                        broadcast_id,
                        status="error",
                        error_message=f"Recovery failed after {max_retries} attempts - HLS stream not available",
                    )

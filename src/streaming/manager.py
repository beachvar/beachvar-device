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

from .camera import CameraConfig, StreamConfig, LiveStreamInfo, StreamMode

logger = logging.getLogger(__name__)

# HLS output directory for local streaming
HLS_OUTPUT_DIR = "/tmp/hls"


@dataclass
class StreamProcess:
    """Active FFmpeg stream process."""

    camera_id: str
    process: subprocess.Popen
    live_stream_id: Optional[str] = None
    started_at: Optional[str] = None
    started_timestamp: float = field(default_factory=time.time)

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
        device_public_url: Optional[str] = None,
        on_stream_status_change: Optional[Callable] = None,
    ):
        """
        Initialize the stream manager.

        Args:
            backend_url: Backend API URL (e.g., https://api.beachvar.com)
            device_token: Device authentication token
            device_public_url: Public URL for this device (e.g., https://device-id.devices.beachvar.com)
            on_stream_status_change: Callback for stream status changes
        """
        self.backend_url = backend_url.rstrip("/")
        self.device_token = device_token
        self.device_public_url = device_public_url.rstrip("/") if device_public_url else None
        self.on_stream_status_change = on_stream_status_change

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
        """Get HTTP headers for API requests."""
        return {
            "X-Device-Token": self.device_token,
            "Content-Type": "application/json",
        }

    async def start(self) -> None:
        """Start the stream manager."""
        self._running = True

        # Load cameras from backend
        await self.refresh_cameras()

        # Recover any active YouTube broadcasts (after deploy/restart)
        await self._recover_youtube_broadcasts()

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

    # ==================== Camera Management ====================

    async def refresh_cameras(self) -> list[CameraConfig]:
        """Fetch cameras from backend and update cache."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/"
                logger.info(f"Fetching cameras from {url}")
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        camera_list = data.get("cameras", [])
                        cameras = [CameraConfig.from_dict(c) for c in camera_list]
                        self._cameras = {c.id: c for c in cameras}
                        logger.info(f"Loaded {len(cameras)} cameras from backend:")
                        for cam in cameras:
                            has_stream = "YES" if cam.has_stream else "NO"
                            logger.info(f"  - {cam.name} (ID: {cam.id[:8]}...) stream={has_stream}")
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
        position: str = "other",
    ) -> Optional[CameraConfig]:
        """
        Register a new camera with the backend.

        Args:
            name: Camera name
            rtsp_url: RTSP URL for the camera
            court_id: Court UUID to associate camera with
            position: Camera position (side1, side2, aerial, other)

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
                    "position": position,
                }

                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=payload
                ) as resp:
                    if resp.status == 201:
                        data = await resp.json()
                        camera = CameraConfig.from_dict(data)
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
                    else:
                        logger.error(f"Failed to get camera {camera_id}: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Error getting camera {camera_id}: {e}")
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

    async def start_stream(self, camera_id: str) -> Optional[LiveStreamInfo]:
        """
        Start streaming from a camera to Cloudflare Stream.

        Args:
            camera_id: Camera UUID to start streaming

        Returns:
            LiveStreamInfo or None on error
        """
        # Get camera config first for logging
        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        logger.info(f"=== Starting stream for {camera_name} ({camera_id}) ===")

        # Check if already streaming
        if camera_id in self._streams and self._streams[camera_id].is_running:
            logger.warning(f"Camera {camera_name} is already streaming")
            return None

        # Get camera config
        if not camera:
            camera = await self.get_camera(camera_id)

        if not camera:
            logger.error(f"Camera {camera_id} not found")
            return None

        if not camera.has_stream:
            logger.error(f"Camera {camera.name} has no stream configured")
            return None

        # Notify backend that stream is starting
        logger.info(f"Notifying backend that {camera.name} is starting...")
        live_stream_info = await self._notify_stream_start(camera_id)
        if not live_stream_info:
            logger.error(f"Failed to notify backend for {camera.name}")
            return None
        logger.info(f"Backend notified for {camera.name}, stream ID: {live_stream_info.id}")

        # Start FFmpeg process
        try:
            logger.info(f"Starting FFmpeg for {camera.name}...")
            process = self._start_ffmpeg(camera)

            self._streams[camera_id] = StreamProcess(
                camera_id=camera_id,
                process=process,
                live_stream_id=live_stream_info.id,
                started_at=live_stream_info.started_at,
            )

            logger.info(f"FFmpeg started for {camera.name} (PID: {process.pid})")

            # Small delay to ensure backend has processed the stream creation
            await asyncio.sleep(0.3)

            # Update backend with FFmpeg PID and status=live
            logger.info(f"Updating status to 'live' for {camera.name}...")
            status_updated = await self._update_stream_status(
                camera_id,
                "live",
                ffmpeg_pid=process.pid,
            )

            if status_updated:
                logger.info(f"=== Stream LIVE for {camera.name} ===")
            else:
                logger.warning(f"=== Stream started but status update failed for {camera.name} ===")

            if self.on_stream_status_change:
                self.on_stream_status_change(camera_id, "live")

            return live_stream_info

        except Exception as e:
            logger.error(f"Failed to start FFmpeg for {camera.name}: {e}")
            await self._update_stream_status(camera_id, "error", error_message=str(e))
            return None

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

        # Notify backend that stream is stopping
        await self._update_stream_status(camera_id, "stopping")

        # Stop FFmpeg process gracefully (same pattern as stream.py)
        try:
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

            logger.info(f"Stopped stream for camera {camera_id}")

        except Exception as e:
            logger.error(f"Error stopping FFmpeg for camera {camera_id}: {e}")

        # Remove from active streams
        del self._streams[camera_id]

        # Clean up HLS files if this was a local HLS stream
        camera = self._cameras.get(camera_id)
        if camera and camera.is_local_hls:
            self.cleanup_hls_files(camera_id)

        # Notify backend that stream has stopped
        await self._notify_stream_stop(camera_id)

        if self.on_stream_status_change:
            self.on_stream_status_change(camera_id, "stopped")

        return True

    async def get_stream_status(self, camera_id: str) -> Optional[LiveStreamInfo]:
        """Get current stream status from backend."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/stream/status/"
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Backend returns {"stream": {...}}
                        stream_data = data.get("stream", data)
                        return LiveStreamInfo.from_dict(stream_data)
                    else:
                        return None
        except Exception as e:
            logger.error(f"Error getting stream status for {camera_id}: {e}")
            return None

    async def get_all_streams(self) -> list[LiveStreamInfo]:
        """Get all active streams from backend."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/streams/"
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        stream_list = data.get("streams", [])
                        return [LiveStreamInfo.from_dict(s) for s in stream_list]
                    else:
                        return []
        except Exception as e:
            logger.error(f"Error getting all streams: {e}")
            return []

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

        Handles URLs like: rtsp://user:pass!word@host:port/path
        """
        # Parse the URL
        match = re.match(
            r'^(rtsp://)?([^:]+):([^@]+)@(.+)$',
            rtsp_url
        )

        if match:
            scheme = match.group(1) or "rtsp://"
            user = match.group(2)
            password = match.group(3)
            rest = match.group(4)

            # URL encode the password
            encoded_password = quote(password, safe='')

            return f"{scheme}{user}:{encoded_password}@{rest}"

        return rtsp_url

    def _start_ffmpeg(self, camera: CameraConfig) -> subprocess.Popen:
        """
        Start FFmpeg process to stream from RTSP to RTMPS or HLS.

        Args:
            camera: Camera configuration with stream details

        Returns:
            FFmpeg subprocess
        """
        rtsp_url = self._encode_rtsp_url(camera.rtsp_url)

        # Determine output based on stream mode
        if camera.is_local_hls:
            cmd = self._build_hls_ffmpeg_cmd(camera, rtsp_url)
            protocol = "HLS (local)"
        else:
            if not camera.stream:
                raise ValueError("Camera has no Cloudflare stream configured")
            cmd = self._build_rtmps_ffmpeg_cmd(camera, rtsp_url)
            protocol = "RTMPS (Cloudflare)"

        logger.info(f"Starting FFmpeg for camera {camera.name}: {rtsp_url} -> {protocol}")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        # Start FFmpeg as subprocess with stderr captured
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        return process

    def _build_rtmps_ffmpeg_cmd(self, camera: CameraConfig, rtsp_url: str) -> list[str]:
        """Build FFmpeg command for RTMPS output (Cloudflare Stream)."""
        output_url = camera.stream.rtmps_full_url

        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",

            # Input options - optimized for RTSP
            "-rtsp_transport", "tcp",
            "-fflags", "+genpts+discardcorrupt",
            "-flags", "low_delay",
            "-use_wallclock_as_timestamps", "1",
            "-i", rtsp_url,

            # Map video and audio
            "-map", "0:v:0",
            "-map", "0:a:0?",

            # Video: copy codec (H.264 passthrough)
            "-c:v", "copy",
            "-bsf:v", "h264_mp4toannexb",

            # Audio: transcode to AAC
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",

            # Output options
            "-max_muxing_queue_size", "1024",
            "-f", "flv",
            output_url,
        ]

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
            "-loglevel", "error",

            # Input options - optimized for RTSP
            "-rtsp_transport", "tcp",
            "-fflags", "+genpts+discardcorrupt",
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

            # HLS output options for live streaming
            "-f", "hls",
            "-hls_time", "2",            # 2-second segments
            "-hls_list_size", "3600",    # Keep last 3600 segments in playlist (2 hours DVR window)
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

    async def _notify_stream_start(self, camera_id: str) -> Optional[LiveStreamInfo]:
        """Notify backend that stream is starting."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/stream/start/"

                # For local HLS, send the HLS URL to the backend
                payload = {}
                camera = self._cameras.get(camera_id)
                if camera and camera.is_local_hls:
                    hls_url = self.get_hls_url(camera_id)
                    if hls_url:
                        payload["local_hls_url"] = hls_url

                async with session.post(
                    url,
                    headers=self._get_headers(),
                    json=payload if payload else None
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        # Backend returns {"stream": {...}, "config": {...}}
                        stream_data = data.get("stream", data)
                        return LiveStreamInfo.from_dict(stream_data)
                    else:
                        error = await resp.text()
                        logger.error(f"Failed to notify stream start: {resp.status} - {error}")
                        return None
        except Exception as e:
            logger.error(f"Error notifying stream start: {e}")
            return None

    async def _notify_stream_stop(self, camera_id: str) -> bool:
        """Notify backend that stream has stopped."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/stream/stop/"
                async with session.post(url, headers=self._get_headers()) as resp:
                    return resp.status in (200, 204)
        except Exception as e:
            logger.error(f"Error notifying stream stop: {e}")
            return False

    async def _update_stream_status(
        self,
        camera_id: str,
        status: str,
        error_message: Optional[str] = None,
        ffmpeg_pid: Optional[int] = None,
        retries: int = 3,
    ) -> bool:
        """Update stream status on backend with retry logic."""
        camera = self._cameras.get(camera_id)
        camera_name = camera.name if camera else camera_id

        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/stream/status/"
                    payload = {"status": status}
                    if error_message:
                        payload["error_message"] = error_message
                    if ffmpeg_pid:
                        payload["ffmpeg_pid"] = ffmpeg_pid

                    async with session.patch(
                        url,
                        headers=self._get_headers(),
                        json=payload
                    ) as resp:
                        if resp.status == 200:
                            logger.info(f"Updated status for {camera_name} to {status}")
                            return True
                        else:
                            error = await resp.text()
                            logger.warning(
                                f"Failed to update status for {camera_name} to {status}: "
                                f"{resp.status} - {error} (attempt {attempt + 1}/{retries})"
                            )
                            if attempt < retries - 1:
                                await asyncio.sleep(0.5 * (attempt + 1))  # Backoff
            except Exception as e:
                logger.error(f"Error updating stream status for {camera_name}: {e} (attempt {attempt + 1}/{retries})")
                if attempt < retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        logger.error(f"Failed to update status for {camera_name} after {retries} attempts")
        return False

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

                        await self._update_stream_status(
                            camera_id,
                            "error",
                            error_message=error_msg,
                        )

                        # Remove from active streams
                        del self._streams[camera_id]

                        if self.on_stream_status_change:
                            self.on_stream_status_change(camera_id, "error")

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
                    if not camera or not camera.is_local_hls:
                        continue

                    # Check if URL needs refresh (every 6 hours)
                    last_refresh = last_url_refresh.get(camera_id, stream.started_timestamp)
                    if current_time - last_refresh >= url_refresh_interval:
                        logger.info(f"Refreshing HLS URL for {camera.name} (URL expiring soon)")
                        await self._refresh_hls_url(camera_id)
                        last_url_refresh[camera_id] = current_time

                # Send stream heartbeats to backend (every 10 seconds)
                for camera_id, stream in list(self._streams.items()):
                    if not stream.is_running:
                        continue

                    last_hb = last_stream_heartbeat.get(camera_id, 0)
                    if current_time - last_hb >= stream_heartbeat_interval:
                        # Send heartbeat (just update status to keep it alive)
                        await self._update_stream_status(camera_id, "live")
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

                        logger.error(f"YouTube stream {broadcast_id} failed: {error_msg}")

                        # Notify backend
                        await self._update_youtube_broadcast_status(
                            broadcast_id,
                            status="error",
                            error_message=error_msg,
                        )

                        # Remove from active YouTube streams
                        del self._youtube_streams[broadcast_id]
                        self._youtube_last_heartbeat.pop(broadcast_id, None)

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
        # Count active streams before refresh
        active_before = len([s for s in self._streams.values() if s.is_running])

        # Refresh camera list from backend
        try:
            await self.refresh_cameras()
        except Exception as e:
            logger.error(f"Failed to refresh cameras during health check: {e}")
            return

        # Count cameras that should be streaming
        cameras_with_stream = [c for c in self._cameras.values() if c.has_stream]
        active_streams = [s for s in self._streams.values() if s.is_running]

        logger.info(
            f"Health check: {len(active_streams)}/{len(cameras_with_stream)} cameras streaming"
        )

        # Check each camera
        cameras_started = 0
        for camera_id, camera in self._cameras.items():
            if not camera.has_stream:
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
            if not camera or not camera.has_stream:
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
        if not camera or not camera.has_stream:
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
            "-loglevel", "error",

            # Input from HLS - use live_start_index to start from current position
            "-live_start_index", "-1",
            "-i", hls_playlist,

            # Copy video and audio (no re-encoding needed, HLS is already H.264/AAC)
            "-c:v", "copy",
            "-c:a", "copy",

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

        process = self._youtube_streams.get(broadcast_id)
        if not process:
            logger.warning(f"No active YouTube stream for broadcast {broadcast_id}")
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

        # Remove from active streams
        del self._youtube_streams[broadcast_id]

        # Notify backend
        await self._update_youtube_broadcast_status(
            broadcast_id,
            status="complete",
        )

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

    async def _recover_youtube_broadcasts(self) -> None:
        """
        Recover active YouTube broadcasts after device restart/deploy.

        Fetches broadcasts in STARTING/LIVE status from backend and attempts
        to restart FFmpeg streaming to YouTube.
        """
        logger.info("Checking for active YouTube broadcasts to recover...")

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/youtube/broadcasts/"
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to fetch active YouTube broadcasts: {resp.status}")
                        return

                    data = await resp.json()
                    broadcasts = data.get("broadcasts", [])

                    if not broadcasts:
                        logger.info("No active YouTube broadcasts to recover")
                        return

                    logger.info(f"Found {len(broadcasts)} YouTube broadcast(s) to recover")

                    for broadcast in broadcasts:
                        broadcast_id = broadcast["id"]
                        camera_id = broadcast["camera_id"]
                        camera_name = broadcast.get("camera_name", camera_id)
                        rtmp_url = broadcast.get("rtmp_url", "")
                        stream_key = broadcast.get("stream_key", "")

                        if not rtmp_url or not stream_key:
                            logger.warning(f"Broadcast {broadcast_id} missing RTMP URL or stream key")
                            await self._update_youtube_broadcast_status(
                                broadcast_id,
                                status="error",
                                error_message="Missing RTMP URL or stream key for recovery",
                            )
                            continue

                        # Check if HLS stream is running for this camera
                        if camera_id not in self._streams or not self._streams[camera_id].is_running:
                            logger.warning(
                                f"Camera {camera_name} not streaming locally, cannot recover YouTube broadcast"
                            )
                            await self._update_youtube_broadcast_status(
                                broadcast_id,
                                status="error",
                                error_message="Camera HLS stream not running for recovery",
                            )
                            continue

                        # Try to restart the YouTube stream
                        logger.info(f"Recovering YouTube broadcast for {camera_name}: {broadcast_id}")
                        success = await self.start_youtube_stream(
                            camera_id=camera_id,
                            broadcast_id=broadcast_id,
                            rtmp_url=rtmp_url,
                            stream_key=stream_key,
                        )

                        if success:
                            logger.info(f"Successfully recovered YouTube broadcast for {camera_name}")
                        else:
                            logger.error(f"Failed to recover YouTube broadcast for {camera_name}")

        except Exception as e:
            logger.error(f"Error recovering YouTube broadcasts: {e}")

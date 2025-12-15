"""
Stream manager for camera management and live streaming.
Handles communication with backend API and FFmpeg processes.
"""

import asyncio
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from urllib.parse import quote

import aiohttp

from .camera import CameraConfig, StreamConfig, LiveStreamInfo

logger = logging.getLogger(__name__)


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
        on_stream_status_change: Optional[Callable] = None,
    ):
        """
        Initialize the stream manager.

        Args:
            backend_url: Backend API URL (e.g., https://api.beachvar.com)
            device_token: Device authentication token
            on_stream_status_change: Callback for stream status changes
        """
        self.backend_url = backend_url.rstrip("/")
        self.device_token = device_token
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
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        camera_list = data.get("cameras", [])
                        cameras = [CameraConfig.from_dict(c) for c in camera_list]
                        self._cameras = {c.id: c for c in cameras}
                        logger.info(f"Loaded {len(cameras)} cameras from backend")
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
        # Check if already streaming
        if camera_id in self._streams and self._streams[camera_id].is_running:
            logger.warning(f"Camera {camera_id} is already streaming")
            return None

        # Get camera config
        camera = self._cameras.get(camera_id)
        if not camera:
            camera = await self.get_camera(camera_id)

        if not camera:
            logger.error(f"Camera {camera_id} not found")
            return None

        if not camera.has_stream:
            logger.error(f"Camera {camera_id} has no stream configured")
            return None

        # Notify backend that stream is starting
        live_stream_info = await self._notify_stream_start(camera_id)
        if not live_stream_info:
            return None

        # Start FFmpeg process
        try:
            process = self._start_ffmpeg(camera)

            self._streams[camera_id] = StreamProcess(
                camera_id=camera_id,
                process=process,
                live_stream_id=live_stream_info.id,
                started_at=live_stream_info.started_at,
            )

            logger.info(f"Started stream for camera {camera_id} (PID: {process.pid})")

            # Update backend with FFmpeg PID
            await self._update_stream_status(
                camera_id,
                "live",
                ffmpeg_pid=process.pid,
            )

            if self.on_stream_status_change:
                self.on_stream_status_change(camera_id, "live")

            return live_stream_info

        except Exception as e:
            logger.error(f"Failed to start FFmpeg for camera {camera_id}: {e}")
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
        Start FFmpeg process to stream from RTSP to RTMPS.

        Args:
            camera: Camera configuration with stream details

        Returns:
            FFmpeg subprocess
        """
        if not camera.stream:
            raise ValueError("Camera has no stream configured")

        rtsp_url = self._encode_rtsp_url(camera.rtsp_url)

        # Use RTMPS for Cloudflare Stream
        output_url = camera.stream.rtmps_full_url
        output_format = "flv"
        protocol = "RTMPS"

        # FFmpeg command for RTSP streaming
        # Using codec copy for minimal CPU usage
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",  # Use error level to reduce stderr buffering

            # Input options - optimized for RTSP (same as stable stream.py)
            "-rtsp_transport", "tcp",  # Use TCP for RTSP (more reliable)
            "-fflags", "+genpts+discardcorrupt",  # Generate PTS, discard corrupt frames
            "-flags", "low_delay",  # Low delay mode
            "-use_wallclock_as_timestamps", "1",  # Use wall clock for timestamps
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
            "-f", output_format,
            output_url,
        ]

        logger.info(f"Starting FFmpeg for camera {camera.name}: {rtsp_url} -> {protocol}")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        # Start FFmpeg as subprocess with stderr captured
        # Note: Not passing stdin (like stream.py) - only stdout and stderr
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        return process

    # ==================== Backend Communication ====================

    async def _notify_stream_start(self, camera_id: str) -> Optional[LiveStreamInfo]:
        """Notify backend that stream is starting."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.backend_url}/api/v1/device/cameras/{camera_id}/stream/start/"
                async with session.post(url, headers=self._get_headers()) as resp:
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
    ) -> bool:
        """Update stream status on backend."""
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
                    return resp.status == 200
        except Exception as e:
            logger.error(f"Error updating stream status: {e}")
            return False

    # ==================== Monitoring ====================

    async def _monitor_streams(self) -> None:
        """Monitor active streams and handle failures with auto-restart."""
        # Track retry counts for each camera
        retry_counts: dict[str, int] = {}
        max_retries = 10  # More retries before giving up
        health_check_interval = 30  # Full health check every 30 seconds
        stable_stream_threshold = 120  # Reset retries after 2 minutes of stable stream
        last_health_check = 0

        while self._running:
            try:
                await asyncio.sleep(1)  # Check every 1 second like stream.py for fast detection

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

                        # Auto-restart with progressive backoff (like stream.py)
                        # Backoff: 3s, 5s, 7s, 9s... max 30s
                        retry_count = retry_counts.get(camera_id, 0)
                        if retry_count < max_retries:
                            retry_counts[camera_id] = retry_count + 1
                            delay = min(3 + (retry_count * 2), 30)  # Progressive backoff, max 30s
                            logger.info(
                                f"Will restart stream for {camera_name} in {delay}s "
                                f"(attempt {retry_count + 1}/{max_retries})"
                            )
                            asyncio.create_task(
                                self._delayed_restart(camera_id, delay)
                            )
                        else:
                            logger.error(
                                f"Max retries ({max_retries}) reached for {camera_name}, "
                                "will retry on next health check"
                            )
                            # Reset retry count so health check can try again
                            retry_counts[camera_id] = 0

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

    async def _delayed_restart(self, camera_id: str, delay: float) -> None:
        """Restart a stream after a delay."""
        await asyncio.sleep(delay)

        if not self._running:
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

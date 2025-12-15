"""
Stream manager for camera management and live streaming.
Handles communication with backend API and FFmpeg processes.
"""

import asyncio
import logging
import os
import signal
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Callable

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

    @property
    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.process.poll() is None


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
    def cameras(self) -> list[CameraConfig]:
        """Get list of registered cameras."""
        return list(self._cameras.values())

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
                        cameras = [CameraConfig.from_dict(c) for c in data]
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

        # Stop FFmpeg process gracefully
        try:
            if stream.is_running:
                # Send SIGINT for graceful shutdown
                stream.process.send_signal(signal.SIGINT)

                # Wait up to 5 seconds for graceful shutdown
                try:
                    stream.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if not responding
                    stream.process.kill()
                    stream.process.wait()

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
                        return LiveStreamInfo.from_dict(data)
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
                        return [LiveStreamInfo.from_dict(s) for s in data]
                    else:
                        return []
        except Exception as e:
            logger.error(f"Error getting all streams: {e}")
            return []

    # ==================== FFmpeg Management ====================

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

        rtsp_url = camera.rtsp_url
        rtmps_url = camera.stream.rtmps_full_url

        # FFmpeg command for RTSP to RTMPS transcoding
        # Using copy codecs for low-latency when possible
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",

            # Input options
            "-rtsp_transport", "tcp",  # Use TCP for RTSP (more reliable)
            "-i", rtsp_url,

            # Output options
            "-c:v", "libx264",  # Re-encode video to H.264
            "-preset", "veryfast",  # Fast encoding for low latency
            "-tune", "zerolatency",  # Optimize for streaming
            "-b:v", "2500k",  # Video bitrate
            "-maxrate", "2500k",
            "-bufsize", "5000k",
            "-g", "60",  # Keyframe interval (2 seconds at 30fps)

            "-c:a", "aac",  # AAC audio
            "-b:a", "128k",  # Audio bitrate
            "-ar", "44100",  # Audio sample rate

            # RTMPS output
            "-f", "flv",
            rtmps_url,
        ]

        logger.debug(f"Starting FFmpeg: {' '.join(cmd[:5])}... -> {rtmps_url[:50]}...")

        # Start FFmpeg as subprocess
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
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
                        return LiveStreamInfo.from_dict(data)
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
        max_retries = 5
        base_delay = 5  # seconds

        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                for camera_id, stream in list(self._streams.items()):
                    if not stream.is_running:
                        # Stream died unexpectedly
                        logger.warning(f"Stream for camera {camera_id} died unexpectedly")

                        # Get return code
                        returncode = stream.process.returncode
                        stderr = ""
                        try:
                            _, stderr = stream.process.communicate(timeout=1)
                            stderr = stderr.decode("utf-8", errors="ignore")[-500:]
                        except Exception:
                            pass

                        # Notify backend of error
                        error_msg = f"FFmpeg exited with code {returncode}"
                        if stderr:
                            error_msg += f": {stderr}"

                        await self._update_stream_status(
                            camera_id,
                            "error",
                            error_message=error_msg,
                        )

                        # Remove from active streams
                        del self._streams[camera_id]

                        if self.on_stream_status_change:
                            self.on_stream_status_change(camera_id, "error")

                        # Auto-restart with exponential backoff
                        retry_count = retry_counts.get(camera_id, 0)
                        if retry_count < max_retries:
                            retry_counts[camera_id] = retry_count + 1
                            delay = base_delay * (2 ** retry_count)
                            logger.info(
                                f"Will restart stream for {camera_id} in {delay}s "
                                f"(attempt {retry_count + 1}/{max_retries})"
                            )
                            asyncio.create_task(
                                self._delayed_restart(camera_id, delay)
                            )
                        else:
                            logger.error(
                                f"Max retries ({max_retries}) reached for camera {camera_id}, "
                                "giving up auto-restart"
                            )

                # Reset retry counts for cameras that have been running for a while
                for camera_id, stream in self._streams.items():
                    if stream.is_running and camera_id in retry_counts:
                        # If stream has been running for 60+ seconds, reset retry count
                        del retry_counts[camera_id]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in stream monitor: {e}")

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

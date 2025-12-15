"""
Camera configuration and stream data models.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class StreamConfig:
    """Cloudflare Stream configuration for a camera."""

    live_input_id: str
    rtmps_url: str
    rtmps_key: str
    srt_url: Optional[str] = None
    srt_passphrase: Optional[str] = None
    playback_hls: Optional[str] = None
    playback_dash: Optional[str] = None

    @property
    def rtmps_full_url(self) -> str:
        """Get full RTMPS URL with stream key."""
        # Remove trailing slash from URL if present to avoid double slashes
        url = self.rtmps_url.rstrip("/")
        return f"{url}/{self.rtmps_key}"


@dataclass
class CameraConfig:
    """Camera configuration from backend."""

    id: str
    name: str
    rtsp_url: str
    position: str
    court_id: str
    court_name: str
    complex_id: str
    complex_name: str
    stream: Optional[StreamConfig] = None

    @classmethod
    def from_dict(cls, data: dict) -> "CameraConfig":
        """Create from API response dict."""
        stream_data = data.get("stream")
        stream = None
        if stream_data and stream_data.get("configured"):
            stream = StreamConfig(
                live_input_id=stream_data.get("live_input_id", ""),
                rtmps_url=stream_data.get("rtmps_url", ""),
                rtmps_key=stream_data.get("rtmps_key", ""),
                srt_url=stream_data.get("srt_url"),
                srt_passphrase=stream_data.get("srt_passphrase"),
                playback_hls=stream_data.get("playback_hls"),
                playback_dash=stream_data.get("playback_dash"),
            )

        court = data.get("court", {})
        complex_data = data.get("complex", {})

        return cls(
            id=data["id"],
            name=data["name"],
            rtsp_url=data["rtsp_url"],
            position=data.get("position", "other"),
            court_id=court.get("id", ""),
            court_name=court.get("name", ""),
            complex_id=complex_data.get("id", ""),
            complex_name=complex_data.get("name", ""),
            stream=stream,
        )

    @property
    def has_stream(self) -> bool:
        """Check if camera has stream configured."""
        return self.stream is not None


@dataclass
class LiveStreamInfo:
    """Information about an active live stream."""

    id: str
    status: str
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    bitrate_kbps: int = 0
    viewers_count: int = 0
    error_message: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "LiveStreamInfo":
        """Create from API response dict."""
        return cls(
            id=data["id"],
            status=data["status"],
            started_at=data.get("started_at"),
            stopped_at=data.get("stopped_at"),
            duration_seconds=data.get("duration_seconds"),
            bitrate_kbps=data.get("bitrate_kbps", 0),
            viewers_count=data.get("viewers_count", 0),
            error_message=data.get("error_message"),
        )

    @property
    def is_active(self) -> bool:
        """Check if stream is currently active."""
        return self.status in ["starting", "live"]

"""
Camera configuration and stream data models.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class CameraConfig:
    """Camera configuration from backend."""

    id: str
    name: str
    rtsp_url: str
    court_id: str
    court_name: str
    complex_id: str
    complex_name: str
    hls_url: str = ""
    is_connected: bool = False
    last_seen_at: Optional[str] = None
    recording_duration_seconds: Optional[int] = None
    hls_playback_delay_seconds: int = 6

    @classmethod
    def from_dict(cls, data: dict) -> "CameraConfig":
        """Create from API response dict."""
        court = data.get("court", {})
        complex_data = data.get("complex", {})

        return cls(
            id=data["id"],
            name=data["name"],
            rtsp_url=data["rtsp_url"],
            court_id=data.get("court_id") or court.get("id", ""),
            court_name=data.get("court_name") or court.get("name", ""),
            complex_id=data.get("complex_id") or complex_data.get("id", ""),
            complex_name=data.get("complex_name") or complex_data.get("name", ""),
            hls_url=data.get("hls_url", ""),
            is_connected=data.get("is_connected", False),
            last_seen_at=data.get("last_seen_at"),
            recording_duration_seconds=data.get("recording_duration_seconds"),
            hls_playback_delay_seconds=data.get("hls_playback_delay_seconds", 6),
        )

    @property
    def has_stream_config(self) -> bool:
        """Check if camera has RTSP URL configured."""
        return bool(self.rtsp_url)

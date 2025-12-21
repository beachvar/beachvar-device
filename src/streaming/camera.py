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
    position: str
    court_id: str
    court_name: str
    complex_id: str
    complex_name: str
    hls_url: str = ""
    is_connected: bool = False
    last_seen_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "CameraConfig":
        """Create from API response dict."""
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
            hls_url=data.get("hls_url", ""),
            is_connected=data.get("is_connected", False),
            last_seen_at=data.get("last_seen_at"),
        )

    @property
    def has_stream_config(self) -> bool:
        """Check if camera has RTSP URL configured."""
        return bool(self.rtsp_url)

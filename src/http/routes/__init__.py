"""
HTTP routes for the device admin API.
"""

from .admin import AdminController
from .cameras import CamerasController
from .streams import StreamsController
from .buttons import ButtonsController
from .logs import LogsController
from .hls import HLSController
from .courts import CourtsController

__all__ = [
    "AdminController",
    "CamerasController",
    "StreamsController",
    "ButtonsController",
    "LogsController",
    "HLSController",
    "CourtsController",
]

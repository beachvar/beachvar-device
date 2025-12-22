"""
HTTP routes for the device admin API.
"""

from .admin import AdminController
from .cameras import CamerasController
from .streams import StreamsController
from .logs import LogsController
from .device_logs import DeviceLogsController
from .hls import HLSController, APIHLSController
from .courts import CourtsController

__all__ = [
    "AdminController",
    "CamerasController",
    "StreamsController",
    "LogsController",
    "DeviceLogsController",
    "HLSController",
    "APIHLSController",
    "CourtsController",
]

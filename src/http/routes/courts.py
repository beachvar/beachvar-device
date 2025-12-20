"""
Courts routes - proxy to backend API.
"""

import base64
import logging
import os

import aiohttp
from litestar import Controller, get
from litestar.exceptions import HTTPException

logger = logging.getLogger(__name__)


def get_auth_headers() -> dict:
    """Get authentication headers for backend API."""
    device_id = os.getenv("DEVICE_ID", "")
    device_token = os.getenv("DEVICE_TOKEN", "")
    credentials = f"{device_id}:{device_token}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }


class CourtsController(Controller):
    """Courts endpoints - proxy to backend."""

    path = "/api/courts"

    @get("/")
    async def list_courts(self) -> dict:
        """List all courts from the device's complex."""
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        if not backend_url:
            raise HTTPException(status_code=503, detail="Backend URL not configured")

        url = f"{backend_url}/api/v1/device/courts/"
        headers = get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error = await response.text()
                        logger.error(f"Failed to fetch courts: {response.status} - {error}")
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Backend error: {response.status}"
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching courts: {e}")
            raise HTTPException(status_code=500, detail=str(e))

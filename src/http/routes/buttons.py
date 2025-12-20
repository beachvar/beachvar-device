"""
GPIO Buttons routes.
"""

import base64
import logging
import os

import aiohttp
from litestar import Controller, get, post, patch, delete
from litestar.exceptions import HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)


class ButtonCreateDTO(BaseModel):
    """DTO for creating a button."""
    button_number: int
    gpio_pin: int
    label: str = ""
    is_active: bool = True


class ButtonUpdateDTO(BaseModel):
    """DTO for updating a button."""
    gpio_pin: Optional[int] = None
    label: Optional[str] = None
    is_active: Optional[bool] = None


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


class ButtonsController(Controller):
    """GPIO button management endpoints."""

    path = "/api/buttons"

    @get("/")
    async def list_buttons(self, gpio_handler: Optional[object] = None) -> dict:
        """List all configured GPIO buttons from backend."""
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        if not backend_url:
            raise HTTPException(status_code=503, detail="Backend URL not configured")

        url = f"{backend_url}/api/v1/device/buttons/"
        headers = get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        buttons = data.get("buttons", [])
                        # Add GPIO monitoring status if handler available
                        if gpio_handler and hasattr(gpio_handler, "buttons"):
                            for btn in buttons:
                                gpio_pin = btn.get("gpio_pin")
                                btn["is_monitoring"] = gpio_pin in gpio_handler.buttons
                        return data
                    else:
                        error = await response.text()
                        logger.error(f"Failed to fetch buttons: {response.status} - {error}")
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Backend error: {response.status}"
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching buttons: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @post("/")
    async def create_button(
        self, data: ButtonCreateDTO, gpio_handler: Optional[object] = None
    ) -> dict:
        """Create a new GPIO button configuration."""
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        if not backend_url:
            raise HTTPException(status_code=503, detail="Backend URL not configured")

        url = f"{backend_url}/api/v1/device/buttons/create/"
        headers = get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=data.model_dump()
                ) as response:
                    result = await response.json()
                    if response.status in (200, 201):
                        # Refresh GPIO handler config
                        if gpio_handler and hasattr(gpio_handler, "refresh_config"):
                            await gpio_handler.refresh_config()
                        return result
                    else:
                        raise HTTPException(
                            status_code=response.status,
                            detail=result.get("error", "Failed to create button")
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Error creating button: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @patch("/{button_id:str}")
    async def update_button(
        self,
        button_id: str,
        data: ButtonUpdateDTO,
        gpio_handler: Optional[object] = None
    ) -> dict:
        """Update a GPIO button configuration."""
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        if not backend_url:
            raise HTTPException(status_code=503, detail="Backend URL not configured")

        url = f"{backend_url}/api/v1/device/buttons/{button_id}/"
        headers = get_auth_headers()

        # Only include non-None fields
        update_data = {k: v for k, v in data.model_dump().items() if v is not None}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    url, headers=headers, json=update_data
                ) as response:
                    result = await response.json()
                    if response.status == 200:
                        # Refresh GPIO handler config
                        if gpio_handler and hasattr(gpio_handler, "refresh_config"):
                            await gpio_handler.refresh_config()
                        return result
                    else:
                        raise HTTPException(
                            status_code=response.status,
                            detail=result.get("error", "Failed to update button")
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Error updating button: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @delete("/{button_id:str}")
    async def delete_button(
        self, button_id: str, gpio_handler: Optional[object] = None
    ) -> None:
        """Delete a GPIO button configuration."""
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        if not backend_url:
            raise HTTPException(status_code=503, detail="Backend URL not configured")

        url = f"{backend_url}/api/v1/device/buttons/{button_id}/"
        headers = get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=headers) as response:
                    if response.status == 204:
                        # Refresh GPIO handler config
                        if gpio_handler and hasattr(gpio_handler, "refresh_config"):
                            await gpio_handler.refresh_config()
                        return None
                    else:
                        try:
                            result = await response.json()
                            raise HTTPException(
                                status_code=response.status,
                                detail=result.get("error", "Failed to delete button")
                            )
                        except Exception:
                            raise HTTPException(
                                status_code=response.status,
                                detail=f"Backend error: {response.status}"
                            )
        except aiohttp.ClientError as e:
            logger.error(f"Error deleting button: {e}")
            raise HTTPException(status_code=500, detail=str(e))

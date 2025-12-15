#!/usr/bin/env python3
"""
BeachVar Device - Main entry point.
Connects to the BeachVar Gateway via WebSocket.
"""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from src.gateway import GatewayClient

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Command handlers
async def handle_get_cameras(params: dict) -> list:
    """Return available cameras on this device."""
    # TODO: Implement actual camera detection
    # For now, return mock data
    logger.info("Getting cameras...")
    return [
        {'id': 'cam1', 'name': 'Camera 1', 'path': '/dev/video0', 'status': 'available'},
    ]


async def handle_start_stream(params: dict) -> dict:
    """Start streaming from a camera."""
    camera_id = params.get('camera_id')
    rtmp_url = params.get('rtmp_url')
    logger.info(f"Starting stream for camera {camera_id} to {rtmp_url}")
    # TODO: Implement actual streaming with ffmpeg
    return {'streaming': True, 'camera_id': camera_id}


async def handle_stop_stream(params: dict) -> dict:
    """Stop streaming from a camera."""
    camera_id = params.get('camera_id')
    logger.info(f"Stopping stream for camera {camera_id}")
    # TODO: Implement actual stream stopping
    return {'streaming': False, 'camera_id': camera_id}


async def handle_restart(params: dict) -> dict:
    """Restart the device."""
    logger.warning("Restart requested!")
    # TODO: Implement actual restart
    return {'restarting': True}


async def main():
    """Main entry point."""
    # Get configuration from environment
    gateway_url = os.getenv('GATEWAY_URL')
    device_id = os.getenv('DEVICE_ID')
    device_token = os.getenv('DEVICE_TOKEN')
    token_file = os.getenv('TOKEN_FILE')

    # Validate configuration
    if not gateway_url:
        logger.error("GATEWAY_URL not set")
        return

    if not device_id:
        logger.error("DEVICE_ID not set")
        return

    if not device_token:
        # Try to read from token file
        if token_file and Path(token_file).exists():
            device_token = Path(token_file).read_text().strip()
            logger.info(f"Loaded token from {token_file}")
        else:
            logger.error("DEVICE_TOKEN not set and no token file found")
            return

    logger.info(f"Starting BeachVar Device")
    logger.info(f"Gateway: {gateway_url}")
    logger.info(f"Device ID: {device_id}")

    # Create client
    client = GatewayClient(
        gateway_url=gateway_url,
        device_id=device_id,
        token=device_token,
        token_file=token_file,
        heartbeat_interval=30,
    )

    # Register command handlers
    client.register_command_handler('get_cameras', handle_get_cameras)
    client.register_command_handler('start_stream', handle_start_stream)
    client.register_command_handler('stop_stream', handle_stop_stream)
    client.register_command_handler('restart', handle_restart)

    # Connect and run
    try:
        await client.connect()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())

#!/usr/bin/env python3
"""
BeachVar Device - Main entry point.
Connects to the BeachVar Gateway via WebSocket.
"""

import asyncio
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

from src.gateway import GatewayClient
from src.tunnel import TunnelManager
from src.http import DeviceHTTPServer

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global instances for signal handling
http_server: DeviceHTTPServer | None = None
tunnel_manager: TunnelManager | None = None
gateway_client: GatewayClient | None = None


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


async def on_tunnel_config(config: dict) -> None:
    """Handle tunnel configuration from gateway."""
    global tunnel_manager

    if tunnel_manager and config:
        logger.info("Received tunnel configuration from gateway")
        if tunnel_manager.configure(config):
            await tunnel_manager.start()


async def main():
    """Main entry point."""
    global http_server, tunnel_manager, gateway_client

    # Get configuration from environment
    gateway_url = os.getenv('GATEWAY_URL')
    device_id = os.getenv('DEVICE_ID')
    device_token = os.getenv('DEVICE_TOKEN')
    token_file = os.getenv('TOKEN_FILE')
    http_port = int(os.getenv('HTTP_PORT', '8080'))

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

    logger.info("Starting BeachVar Device")
    logger.info(f"Gateway: {gateway_url}")
    logger.info(f"Device ID: {device_id}")

    # Start HTTP server (for tunnel access)
    # Use 0.0.0.0 to allow access from cloudflared and within Docker
    http_server = DeviceHTTPServer(host="0.0.0.0", port=http_port)
    await http_server.start()

    # Initialize tunnel manager
    tunnel_manager = TunnelManager(local_port=http_port)

    # Create gateway client
    gateway_client = GatewayClient(
        gateway_url=gateway_url,
        device_id=device_id,
        token=device_token,
        token_file=token_file,
        heartbeat_interval=10,
    )

    # Register command handlers
    gateway_client.register_command_handler('get_cameras', handle_get_cameras)
    gateway_client.register_command_handler('start_stream', handle_start_stream)
    gateway_client.register_command_handler('stop_stream', handle_stop_stream)
    gateway_client.register_command_handler('restart', handle_restart)

    # Register tunnel config callback
    gateway_client.on_tunnel_config = on_tunnel_config

    # Connect and run
    try:
        await gateway_client.connect()
    except asyncio.CancelledError:
        logger.info("Shutting down...")
    finally:
        # Cleanup
        if tunnel_manager:
            await tunnel_manager.stop()
        if http_server:
            await http_server.stop()
        if gateway_client:
            await gateway_client.disconnect()


def handle_signal(sig, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {sig}, shutting down...")
    # Cancel all tasks
    for task in asyncio.all_tasks():
        task.cancel()


if __name__ == '__main__':
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(main())

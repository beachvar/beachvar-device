#!/usr/bin/env python3
"""
BeachVar Device - Main entry point.

This device is responsible for:
1. Maintaining continuous live streams from all registered cameras to Cloudflare Stream
2. Auto-restarting streams if they fail
3. Reporting status to the backend

Future: Handle button triggers for recording
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
from src.streaming import StreamManager

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
stream_manager: StreamManager | None = None


# Command handlers for gateway commands
async def handle_get_status(params: dict) -> dict:
    """Return device and stream status."""
    global stream_manager
    if not stream_manager:
        return {"error": "Stream manager not initialized"}

    return {
        "cameras": len(stream_manager.cameras),
        "active_streams": len(stream_manager.active_streams),
        "streams": stream_manager.active_streams,
    }


async def handle_restart_stream(params: dict) -> dict:
    """Restart a specific stream."""
    global stream_manager
    camera_id = params.get('camera_id')

    if not stream_manager or not camera_id:
        return {"error": "Invalid request"}

    # Stop if running
    if camera_id in stream_manager.active_streams:
        await stream_manager.stop_stream(camera_id)

    # Start again
    result = await stream_manager.start_stream(camera_id)
    return {"success": result is not None, "camera_id": camera_id}


async def handle_refresh_cameras(params: dict) -> dict:
    """Refresh camera list and start streams for new cameras."""
    global stream_manager
    if not stream_manager:
        return {"error": "Stream manager not initialized"}

    # Get current camera IDs before refresh
    old_camera_ids = set(stream_manager.cameras.keys())

    # Refresh camera list from backend
    cameras = await stream_manager.refresh_cameras()

    # Find new cameras
    new_camera_ids = set(c.id for c in cameras) - old_camera_ids
    started = 0

    # Start streams for new cameras that have stream configured
    for camera in cameras:
        if camera.id in new_camera_ids and camera.has_stream:
            logger.info(f"Starting stream for new camera: {camera.name} ({camera.id})")
            try:
                result = await stream_manager.start_stream(camera.id)
                if result:
                    started += 1
                    logger.info(f"Stream started for {camera.name}")
            except Exception as e:
                logger.error(f"Error starting stream for {camera.name}: {e}")

    return {
        "cameras": len(cameras),
        "new_cameras": len(new_camera_ids),
        "streams_started": started,
    }


async def handle_camera_created(params: dict) -> dict:
    """Handle camera created event - fetch camera and start stream."""
    global stream_manager
    camera_id = params.get("camera_id")

    if not stream_manager or not camera_id:
        return {"error": "Invalid request"}

    logger.info(f"Camera created event received: {camera_id}")

    # Fetch the new camera from backend
    camera = await stream_manager.get_camera(camera_id)
    if not camera:
        return {"error": f"Camera {camera_id} not found"}

    # Start stream if configured
    if camera.has_stream:
        logger.info(f"Starting stream for new camera: {camera.name}")
        result = await stream_manager.start_stream(camera_id)
        return {
            "success": result is not None,
            "camera_id": camera_id,
            "camera_name": camera.name,
            "stream_started": result is not None,
        }

    return {
        "success": True,
        "camera_id": camera_id,
        "camera_name": camera.name,
        "stream_started": False,
        "message": "Camera has no stream configured",
    }


async def handle_camera_deleted(params: dict) -> dict:
    """Handle camera deleted event - stop stream and remove from cache."""
    global stream_manager
    camera_id = params.get("camera_id")

    if not stream_manager or not camera_id:
        return {"error": "Invalid request"}

    logger.info(f"Camera deleted event received: {camera_id}")

    # Stop stream if running
    stream_stopped = False
    if camera_id in stream_manager.active_streams:
        logger.info(f"Stopping stream for deleted camera: {camera_id}")
        await stream_manager.stop_stream(camera_id)
        stream_stopped = True

    # Remove from camera cache
    stream_manager.remove_camera(camera_id)

    return {
        "success": True,
        "camera_id": camera_id,
        "stream_stopped": stream_stopped,
    }


async def handle_camera_updated(params: dict) -> dict:
    """Handle camera updated event - refresh camera and restart stream if needed."""
    global stream_manager
    camera_id = params.get("camera_id")

    if not stream_manager or not camera_id:
        return {"error": "Invalid request"}

    logger.info(f"Camera updated event received: {camera_id}")

    # Check if stream was running
    was_streaming = camera_id in stream_manager.active_streams

    # Stop current stream if running
    if was_streaming:
        logger.info(f"Stopping stream for camera update: {camera_id}")
        await stream_manager.stop_stream(camera_id)

    # Fetch updated camera from backend
    camera = await stream_manager.get_camera(camera_id)
    if not camera:
        return {"error": f"Camera {camera_id} not found"}

    # Restart stream if it was running and still has stream config
    stream_restarted = False
    if was_streaming and camera.has_stream:
        logger.info(f"Restarting stream for updated camera: {camera.name}")
        result = await stream_manager.start_stream(camera_id)
        stream_restarted = result is not None

    return {
        "success": True,
        "camera_id": camera_id,
        "camera_name": camera.name,
        "was_streaming": was_streaming,
        "stream_restarted": stream_restarted,
    }


async def on_tunnel_config(config: dict) -> None:
    """Handle tunnel configuration from gateway."""
    global tunnel_manager, stream_manager

    if tunnel_manager and config:
        logger.info("Received tunnel configuration from gateway")
        if tunnel_manager.configure(config):
            await tunnel_manager.start()

            # Update stream manager with public URL for HLS signing
            if stream_manager:
                public_url = tunnel_manager.get_public_url()
                if public_url:
                    stream_manager.device_public_url = public_url
                    logger.info(f"Stream manager updated with public URL: {public_url}")


async def auto_start_streams() -> None:
    """Auto-start streams for all registered cameras."""
    global stream_manager

    if not stream_manager:
        return

    logger.info("=" * 50)
    logger.info("Auto-starting streams for registered cameras...")
    logger.info("=" * 50)

    # Get all cameras from backend
    cameras = await stream_manager.refresh_cameras()

    if not cameras:
        logger.info("No cameras registered, skipping auto-start")
        return

    # Filter cameras that have stream configured
    cameras_with_stream = [c for c in cameras if c.has_stream]
    logger.info(f"Found {len(cameras_with_stream)} cameras with stream config")

    # Start streams for cameras that have stream configured
    started_count = 0
    for i, camera in enumerate(cameras_with_stream):
        logger.info(f"[{i + 1}/{len(cameras_with_stream)}] Starting stream for camera: {camera.name}")
        try:
            result = await stream_manager.start_stream(camera.id)
            if result:
                started_count += 1
                logger.info(f"[{i + 1}/{len(cameras_with_stream)}] Stream started successfully for {camera.name}")
            else:
                logger.warning(f"[{i + 1}/{len(cameras_with_stream)}] Failed to start stream for {camera.name}")

            # Small delay between starting cameras to avoid race conditions
            if i < len(cameras_with_stream) - 1:
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"[{i + 1}/{len(cameras_with_stream)}] Error starting stream for {camera.name}: {e}")

    logger.info("=" * 50)
    logger.info(f"Auto-start complete: {started_count}/{len(cameras_with_stream)} streams started")
    logger.info("=" * 50)


async def main():
    """Main entry point."""
    global http_server, tunnel_manager, gateway_client, stream_manager

    # Get configuration from environment
    gateway_url = os.getenv('GATEWAY_URL')
    device_id = os.getenv('DEVICE_ID')
    device_token = os.getenv('DEVICE_TOKEN')
    token_file = os.getenv('TOKEN_FILE')
    http_port = int(os.getenv('HTTP_PORT', '8080'))
    backend_url = os.getenv('BACKEND_URL')

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

    if not backend_url:
        logger.error("BACKEND_URL not set")
        return

    logger.info("Starting BeachVar Device")
    logger.info(f"Gateway: {gateway_url}")
    logger.info(f"Backend: {backend_url}")
    logger.info(f"Device ID: {device_id}")

    # Initialize stream manager
    stream_manager = StreamManager(
        backend_url=backend_url,
        device_token=device_token,
    )
    await stream_manager.start()

    # Start HTTP server (for tunnel access)
    # Use 0.0.0.0 to allow access from cloudflared and within Docker
    http_server = DeviceHTTPServer(
        host="0.0.0.0",
        port=http_port,
        stream_manager=stream_manager,
        device_token=device_token,
    )
    await http_server.start()

    # Auto-start streams for registered cameras
    await auto_start_streams()

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
    gateway_client.register_command_handler('get_status', handle_get_status)
    gateway_client.register_command_handler('restart_stream', handle_restart_stream)
    gateway_client.register_command_handler('refresh_cameras', handle_refresh_cameras)
    gateway_client.register_command_handler('camera_created', handle_camera_created)
    gateway_client.register_command_handler('camera_deleted', handle_camera_deleted)
    gateway_client.register_command_handler('camera_updated', handle_camera_updated)

    # Register tunnel config callback
    gateway_client.on_tunnel_config = on_tunnel_config

    # Connect and run
    try:
        await gateway_client.connect()
    except asyncio.CancelledError:
        logger.info("Shutting down...")
    finally:
        # Cleanup
        if stream_manager:
            await stream_manager.stop()
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

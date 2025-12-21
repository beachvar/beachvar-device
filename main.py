#!/usr/bin/env python3
"""
BeachVar Device - Main entry point.

This device is responsible for:
1. Maintaining continuous live streams from all registered cameras via local HLS
2. Auto-restarting streams if they fail
3. Reporting connection status to the backend
4. Serving HLS streams via HTTP
"""

import asyncio
import logging
import os
import signal
from pathlib import Path

import uvloop
from dotenv import load_dotenv

import uvicorn

from src.gateway import GatewayClient
from src.gpio import GPIOButtonHandler
from src.http import create_app
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
http_server: uvicorn.Server | None = None
http_server_task: asyncio.Task | None = None
gateway_client: GatewayClient | None = None
stream_manager: StreamManager | None = None
gpio_handler: GPIOButtonHandler | None = None


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
        if camera.id in new_camera_ids and camera.has_stream_config:
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
    if camera.has_stream_config:
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
    if was_streaming and camera.has_stream_config:
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


async def handle_start_youtube_stream(params: dict) -> dict:
    """Handle start YouTube stream command from backend."""
    global stream_manager

    camera_id = params.get("camera_id")
    broadcast_id = params.get("broadcast_id")
    rtmp_url = params.get("rtmp_url")
    stream_key = params.get("stream_key")

    if not stream_manager:
        return {"error": "Stream manager not initialized"}

    if not all([camera_id, broadcast_id, rtmp_url, stream_key]):
        return {"error": "Missing required parameters"}

    logger.info(f"Starting YouTube stream for camera {camera_id}, broadcast {broadcast_id}")

    result = await stream_manager.start_youtube_stream(
        camera_id=camera_id,
        broadcast_id=broadcast_id,
        rtmp_url=rtmp_url,
        stream_key=stream_key,
    )

    return {
        "success": result,
        "camera_id": camera_id,
        "broadcast_id": broadcast_id,
    }


async def handle_stop_youtube_stream(params: dict) -> dict:
    """Handle stop YouTube stream command from backend."""
    global stream_manager

    camera_id = params.get("camera_id")
    broadcast_id = params.get("broadcast_id")

    if not stream_manager:
        return {"error": "Stream manager not initialized"}

    if not all([camera_id, broadcast_id]):
        return {"error": "Missing required parameters"}

    logger.info(f"Stopping YouTube stream for camera {camera_id}, broadcast {broadcast_id}")

    result = await stream_manager.stop_youtube_stream(
        camera_id=camera_id,
        broadcast_id=broadcast_id,
    )

    return {
        "success": result,
        "camera_id": camera_id,
        "broadcast_id": broadcast_id,
    }


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
    cameras_with_stream = [c for c in cameras if c.has_stream_config]
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
    global http_server, gateway_client, stream_manager, gpio_handler

    # Get configuration from environment
    gateway_url = os.getenv('GATEWAY_URL')
    device_id = os.getenv('DEVICE_ID')
    device_token = os.getenv('DEVICE_TOKEN')
    token_file = os.getenv('TOKEN_FILE', '.tokens')
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
        device_id=device_id,
    )
    await stream_manager.start()

    # Initialize GPIO button handler
    gpio_handler = GPIOButtonHandler(
        backend_url=backend_url,
        device_id=device_id,
        device_token=device_token,
    )
    gpio_started = await gpio_handler.start()
    if gpio_started:
        logger.info("GPIO button handler initialized successfully")
    else:
        logger.info("GPIO not available - running without button support")

    # Create Litestar app and start HTTP server
    app = create_app(
        stream_manager=stream_manager,
    )

    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=http_port,
        log_level="info",
        access_log=False,
    )
    http_server = uvicorn.Server(config)

    # Start server in background task
    global http_server_task
    http_server_task = asyncio.create_task(http_server.serve())
    logger.info(f"HTTP server started on http://0.0.0.0:{http_port}")

    # Auto-start streams for registered cameras
    await auto_start_streams()

    # Recover active YouTube broadcasts (after HLS streams are started)
    if stream_manager:
        await stream_manager.recover_youtube_broadcasts()

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
    gateway_client.register_command_handler('start_youtube_stream', handle_start_youtube_stream)
    gateway_client.register_command_handler('stop_youtube_stream', handle_stop_youtube_stream)

    # Connect and run
    try:
        await gateway_client.connect()
    except asyncio.CancelledError:
        logger.info("Shutting down...")
    finally:
        # Cleanup
        if gpio_handler:
            await gpio_handler.stop()
        if stream_manager:
            await stream_manager.stop()
        if http_server:
            http_server.should_exit = True
            # Wait for server task to complete
            try:
                await asyncio.wait_for(http_server_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
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

    # Use uvloop for better async performance
    uvloop.install()
    asyncio.run(main())

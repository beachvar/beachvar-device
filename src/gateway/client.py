"""
WebSocket client for connecting to BeachVar Gateway.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional, Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class GatewayClient:
    """
    WebSocket client for BeachVar Gateway communication.
    Handles authentication, heartbeats, and command handling.
    """

    def __init__(
        self,
        gateway_url: str,
        device_id: str,
        token: str,
        token_file: Optional[str] = None,
        heartbeat_interval: int = 30,
        reconnect_delay: int = 5,
        max_reconnect_delay: int = 300,
    ):
        """
        Initialize the gateway client.

        Args:
            gateway_url: WebSocket URL of the gateway (e.g., wss://gateway.beachvar.com)
            device_id: Unique device identifier
            token: Authentication token
            token_file: Path to file where new tokens should be saved (for rotation)
            heartbeat_interval: Seconds between heartbeats
            reconnect_delay: Initial reconnect delay in seconds
            max_reconnect_delay: Maximum reconnect delay in seconds
        """
        self.gateway_url = gateway_url.rstrip('/')
        self.device_id = device_id
        self.token = token
        self.token_file = token_file
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay

        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.authenticated = False
        self.running = False
        self.start_time = time.time()

        # Command handlers
        self._command_handlers: dict[str, Callable] = {}

        # Pending command responses
        self._pending_responses: dict[str, asyncio.Future] = {}

        # Tunnel config callback
        self.on_tunnel_config: Optional[Callable] = None

    def register_command_handler(self, action: str, handler: Callable):
        """
        Register a handler for a specific command action.

        Args:
            action: Command action name (e.g., 'start_stream', 'get_cameras')
            handler: Async function to handle the command
        """
        self._command_handlers[action] = handler

    async def connect(self):
        """Connect to the gateway and start the main loop."""
        self.running = True
        current_delay = self.reconnect_delay

        while self.running:
            try:
                ws_url = f"{self.gateway_url}/ws/{self.device_id}"
                logger.info(f"Connecting to gateway: {ws_url}")

                async with websockets.connect(ws_url) as websocket:
                    self.websocket = websocket
                    current_delay = self.reconnect_delay  # Reset delay on successful connection

                    # Authenticate
                    if await self._authenticate():
                        logger.info("Authentication successful")

                        # Start heartbeat task
                        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                        try:
                            # Main message loop
                            await self._message_loop()
                        finally:
                            heartbeat_task.cancel()
                            try:
                                await heartbeat_task
                            except asyncio.CancelledError:
                                pass
                    else:
                        logger.error("Authentication failed")

            except ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}")
            except Exception as e:
                logger.error(f"Connection error: {e}")

            self.websocket = None
            self.authenticated = False

            if self.running:
                logger.info(f"Reconnecting in {current_delay} seconds...")
                await asyncio.sleep(current_delay)
                current_delay = min(current_delay * 2, self.max_reconnect_delay)

    async def disconnect(self):
        """Disconnect from the gateway."""
        self.running = False
        if self.websocket:
            await self.websocket.close()

    async def _authenticate(self) -> bool:
        """Send authentication message and wait for result."""
        if not self.websocket:
            return False

        auth_message = {
            'type': 'auth',
            'payload': {
                'deviceId': self.device_id,
                'token': self.token,
            }
        }

        await self.websocket.send(json.dumps(auth_message))

        try:
            response = await asyncio.wait_for(
                self.websocket.recv(),
                timeout=10.0
            )
            data = json.loads(response)

            if data.get('type') == 'auth_result':
                payload = data.get('payload', {})

                if payload.get('success'):
                    self.authenticated = True

                    # Handle token rotation
                    new_token = payload.get('newToken')
                    if new_token:
                        self.token = new_token
                        self._save_token(new_token)
                        logger.info("Token rotated successfully")

                    # Handle tunnel configuration
                    tunnel_config = payload.get('tunnel')
                    if tunnel_config and self.on_tunnel_config:
                        asyncio.create_task(self.on_tunnel_config(tunnel_config))

                    return True
                else:
                    logger.error(f"Auth failed: {payload.get('error')}")
                    return False

        except asyncio.TimeoutError:
            logger.error("Authentication timeout")
            return False

        return False

    def _save_token(self, token: str):
        """Save new token to file for persistence."""
        if self.token_file:
            try:
                Path(self.token_file).write_text(token)
                logger.info(f"Token saved to {self.token_file}")
            except Exception as e:
                logger.error(f"Failed to save token: {e}")

    async def _heartbeat_loop(self):
        """Send periodic heartbeats."""
        while self.running and self.authenticated:
            try:
                await self._send_heartbeat()
                await asyncio.sleep(self.heartbeat_interval)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                break

    async def _send_heartbeat(self):
        """Send a heartbeat message."""
        if not self.websocket or not self.authenticated:
            return

        uptime = int(time.time() - self.start_time)

        # Get system stats (optional)
        stats = self._get_system_stats()

        heartbeat_message = {
            'type': 'heartbeat',
            'payload': {
                'uptime': uptime,
                **stats
            }
        }

        await self.websocket.send(json.dumps(heartbeat_message))

    def _get_system_stats(self) -> dict:
        """Get system statistics (CPU, memory, temperature)."""
        stats = {}

        try:
            # CPU usage
            with open('/proc/loadavg', 'r') as f:
                load = float(f.read().split()[0])
                stats['cpuUsage'] = load

            # Memory usage
            with open('/proc/meminfo', 'r') as f:
                meminfo = f.read()
                total = int([line for line in meminfo.split('\n') if 'MemTotal' in line][0].split()[1])
                available = int([line for line in meminfo.split('\n') if 'MemAvailable' in line][0].split()[1])
                stats['memoryUsage'] = (total - available) / total * 100

            # Temperature (Raspberry Pi specific)
            temp_path = '/sys/class/thermal/thermal_zone0/temp'
            if os.path.exists(temp_path):
                with open(temp_path, 'r') as f:
                    stats['temperature'] = int(f.read()) / 1000

        except Exception:
            pass  # Stats are optional

        return stats

    async def _message_loop(self):
        """Main message receiving loop."""
        if not self.websocket:
            return

        async for message in self.websocket:
            try:
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON message: {message}")
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _handle_message(self, data: dict):
        """Handle incoming messages."""
        msg_type = data.get('type')

        if msg_type == 'command':
            await self._handle_command(data)
        elif msg_type == 'heartbeat_ack':
            pass  # Heartbeat acknowledged
        else:
            logger.debug(f"Unknown message type: {msg_type}")

    async def _handle_command(self, data: dict):
        """Handle a command from the gateway."""
        request_id = data.get('requestId')
        payload = data.get('payload', {})
        action = payload.get('action')
        params = payload.get('params', {})

        logger.info(f"Received command: {action} (request_id: {request_id})")

        result = None
        error = None

        handler = self._command_handlers.get(action)
        if handler:
            try:
                result = await handler(params)
            except Exception as e:
                logger.error(f"Command handler error: {e}")
                error = str(e)
        else:
            error = f"Unknown action: {action}"
            logger.warning(error)

        # Send response
        response = {
            'type': 'command_response',
            'requestId': request_id,
            'payload': {
                'success': error is None,
                'data': result,
                'error': error,
            }
        }

        if self.websocket:
            await self.websocket.send(json.dumps(response))

    async def send_cameras(self, cameras: list[dict]):
        """
        Report available cameras to the gateway.

        Args:
            cameras: List of camera info dicts with id, name, path, status
        """
        if not self.websocket or not self.authenticated:
            return

        message = {
            'type': 'cameras',
            'payload': {
                'cameras': cameras
            }
        }

        await self.websocket.send(json.dumps(message))

    async def send_stream_status(self, camera_id: str, status: str, error: Optional[str] = None):
        """
        Report stream status to the gateway.

        Args:
            camera_id: Camera identifier
            status: 'started', 'stopped', or 'error'
            error: Error message if status is 'error'
        """
        if not self.websocket or not self.authenticated:
            return

        message = {
            'type': 'stream_status',
            'payload': {
                'cameraId': camera_id,
                'status': status,
                'error': error,
            }
        }

        await self.websocket.send(json.dumps(message))


async def main():
    """Example usage."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    gateway_url = os.getenv('GATEWAY_URL', 'ws://localhost:8787')
    device_id = os.getenv('DEVICE_ID', 'test-device')
    token = os.getenv('DEVICE_TOKEN', 'test-token')

    client = GatewayClient(
        gateway_url=gateway_url,
        device_id=device_id,
        token=token,
    )

    # Register command handlers
    async def handle_get_cameras(params):
        """Example handler for get_cameras command."""
        return [
            {'id': 'cam1', 'name': 'Camera 1', 'path': '/dev/video0', 'status': 'available'},
            {'id': 'cam2', 'name': 'Camera 2', 'path': '/dev/video1', 'status': 'available'},
        ]

    async def handle_start_stream(params):
        """Example handler for start_stream command."""
        camera_id = params.get('camera_id')
        logger.info(f"Starting stream for camera: {camera_id}")
        return {'streaming': True, 'camera_id': camera_id}

    async def handle_stop_stream(params):
        """Example handler for stop_stream command."""
        camera_id = params.get('camera_id')
        logger.info(f"Stopping stream for camera: {camera_id}")
        return {'streaming': False, 'camera_id': camera_id}

    client.register_command_handler('get_cameras', handle_get_cameras)
    client.register_command_handler('start_stream', handle_start_stream)
    client.register_command_handler('stop_stream', handle_stop_stream)

    # Connect and run
    try:
        await client.connect()
    except KeyboardInterrupt:
        await client.disconnect()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())

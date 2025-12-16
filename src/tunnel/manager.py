"""
Cloudflare Tunnel manager.
Handles cloudflared process lifecycle for device remote access.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TunnelConfig:
    """Tunnel configuration from gateway."""
    tunnel_id: str
    tunnel_token: str
    hostname: str


class TunnelManager:
    """Manages Cloudflare Tunnel (cloudflared) process."""

    def __init__(self, local_port: int = 8080):
        self.local_port = local_port
        self.config: TunnelConfig | None = None
        self.process: subprocess.Popen | None = None
        self._running = False
        self._monitor_task: asyncio.Task | None = None
        self._restart_count = 0
        self._last_restart_time = 0.0

    @property
    def is_running(self) -> bool:
        """Check if tunnel process is running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def _check_process_health(self) -> bool:
        """
        Check if the tunnel process is healthy.
        Returns False if process died or is stuck.
        """
        if self.process is None:
            return False

        poll_result = self.process.poll()
        if poll_result is not None:
            # Process exited
            logger.warning(f"Tunnel process exited with code: {poll_result}")
            return False

        return True

    def configure(self, config: dict) -> bool:
        """
        Configure tunnel from gateway response.

        Args:
            config: Dict with tunnelId, tunnelToken, hostname

        Returns:
            True if configuration is valid
        """
        try:
            self.config = TunnelConfig(
                tunnel_id=config['tunnelId'],
                tunnel_token=config['tunnelToken'],
                hostname=config['hostname'],
            )
            logger.info(f"Tunnel configured: {self.config.hostname}")
            return True
        except KeyError as e:
            logger.error(f"Invalid tunnel config, missing: {e}")
            return False

    def _find_cloudflared(self) -> str | None:
        """Find cloudflared binary."""
        # Check common locations
        paths = [
            "/usr/local/bin/cloudflared",
            "/usr/bin/cloudflared",
            os.path.expanduser("~/.cloudflared/cloudflared"),
            shutil.which("cloudflared"),
        ]

        for path in paths:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return path

        return None

    async def start(self) -> bool:
        """
        Start the cloudflared tunnel.

        Returns:
            True if tunnel started successfully
        """
        if not self.config:
            logger.error("Tunnel not configured")
            return False

        if self.is_running:
            logger.warning("Tunnel already running")
            return True

        cloudflared = self._find_cloudflared()
        if not cloudflared:
            logger.error("cloudflared not found. Install it: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
            return False

        try:
            # Run cloudflared with tunnel token
            # The token contains all the configuration needed
            cmd = [
                cloudflared,
                "tunnel",
                "--no-autoupdate",
                "run",
                "--token", self.config.tunnel_token,
            ]

            logger.info(f"Starting tunnel: {self.config.hostname} -> localhost:{self.local_port}")

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Don't inherit env to avoid leaking secrets
            )

            # Wait a bit and check if process started
            await asyncio.sleep(2)

            if self.process.poll() is not None:
                # Process died
                stderr = self.process.stderr.read().decode() if self.process.stderr else ""
                logger.error(f"Tunnel failed to start: {stderr}")
                return False

            self._running = True
            logger.info(f"Tunnel started: https://{self.config.hostname}")

            # Start monitoring task if not already running
            if self._monitor_task is None or self._monitor_task.done():
                self._monitor_task = asyncio.create_task(self._monitor())
                logger.debug("Started tunnel monitor task")

            return True

        except Exception as e:
            logger.error(f"Failed to start tunnel: {e}")
            return False

    async def stop(self) -> None:
        """Stop the cloudflared tunnel."""
        self._running = False

        # Cancel monitor task
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        if self.process:
            try:
                self.process.terminate()
                # Wait for graceful shutdown
                await asyncio.sleep(2)
                if self.process.poll() is None:
                    self.process.kill()
                logger.info("Tunnel stopped")
            except Exception as e:
                logger.error(f"Error stopping tunnel: {e}")
            finally:
                self.process = None

    async def restart(self) -> bool:
        """Restart the tunnel."""
        await self.stop()
        await asyncio.sleep(1)
        return await self.start()

    async def _start_tunnel_process(self) -> bool:
        """
        Start only the cloudflared process (used by monitor for restarts).
        Does not start a new monitor task.
        """
        if not self.config:
            logger.error("Tunnel not configured")
            return False

        cloudflared = self._find_cloudflared()
        if not cloudflared:
            logger.error("cloudflared not found")
            return False

        try:
            cmd = [
                cloudflared,
                "tunnel",
                "--no-autoupdate",
                "run",
                "--token", self.config.tunnel_token,
            ]

            logger.info(f"Starting tunnel process: {self.config.hostname} -> localhost:{self.local_port}")

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Wait a bit and check if process started
            await asyncio.sleep(3)

            if self.process.poll() is not None:
                # Process died
                stderr = self.process.stderr.read().decode() if self.process.stderr else ""
                logger.error(f"Tunnel process failed to start: {stderr}")
                self.process = None
                return False

            logger.info(f"Tunnel process started: https://{self.config.hostname}")
            return True

        except Exception as e:
            logger.error(f"Failed to start tunnel process: {e}")
            return False

    async def _monitor(self) -> None:
        """Monitor tunnel process and restart if needed with exponential backoff."""
        consecutive_failures = 0
        max_backoff = 300  # Max 5 minutes between retries
        base_delay = 5  # Start with 5 seconds

        logger.info("Tunnel monitor started")

        while self._running:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds

                if not self._running:
                    break

                if not self._check_process_health():
                    consecutive_failures += 1
                    current_time = time.time()

                    # Calculate backoff delay with exponential increase
                    # 5s, 10s, 20s, 40s, 80s, 160s, 300s (capped)
                    delay = min(base_delay * (2 ** (consecutive_failures - 1)), max_backoff)

                    logger.warning(
                        f"Tunnel not healthy (failure #{consecutive_failures}), "
                        f"restarting in {delay}s..."
                    )

                    # Kill any zombie process
                    if self.process:
                        try:
                            self.process.kill()
                        except Exception:
                            pass
                        self.process = None

                    await asyncio.sleep(delay)

                    if not self._running:
                        break

                    # Try to restart
                    logger.info(f"Attempting tunnel restart (attempt #{consecutive_failures})...")
                    success = await self._start_tunnel_process()

                    if success:
                        logger.info("Tunnel restarted successfully")
                        # Reset failures after stable period
                        self._last_restart_time = current_time
                    else:
                        logger.error("Tunnel restart failed")
                else:
                    # Tunnel is healthy - reset failure counter after 2 minutes of stability
                    if consecutive_failures > 0:
                        current_time = time.time()
                        if current_time - self._last_restart_time > 120:
                            logger.info("Tunnel stable for 2 minutes, resetting failure counter")
                            consecutive_failures = 0

            except asyncio.CancelledError:
                logger.info("Tunnel monitor cancelled")
                break
            except Exception as e:
                logger.error(f"Error in tunnel monitor: {e}")
                await asyncio.sleep(10)

        logger.info("Tunnel monitor stopped")

    def get_public_url(self) -> str | None:
        """Get the public URL for this tunnel."""
        if self.config:
            return f"https://{self.config.hostname}"
        return None

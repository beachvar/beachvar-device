"""
Cloudflare Tunnel manager.
Handles cloudflared process lifecycle for device remote access.
"""

import asyncio
import logging
import os
import shutil
import subprocess
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

    @property
    def is_running(self) -> bool:
        """Check if tunnel is running."""
        if self.process is None:
            return False
        return self.process.poll() is None

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

            # Start monitoring task
            asyncio.create_task(self._monitor())

            return True

        except Exception as e:
            logger.error(f"Failed to start tunnel: {e}")
            return False

    async def stop(self) -> None:
        """Stop the cloudflared tunnel."""
        self._running = False

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

    async def _monitor(self) -> None:
        """Monitor tunnel process and restart if needed."""
        while self._running:
            await asyncio.sleep(10)

            if not self.is_running and self._running:
                logger.warning("Tunnel died, restarting...")
                await asyncio.sleep(5)
                await self.start()

    def get_public_url(self) -> str | None:
        """Get the public URL for this tunnel."""
        if self.config:
            return f"https://{self.config.hostname}"
        return None

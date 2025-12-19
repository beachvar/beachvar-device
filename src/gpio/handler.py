"""
GPIO Button Handler for Raspberry Pi.

This module handles physical GPIO button presses and sends events to the backend.
Uses lgpio library for GPIO control on Raspberry Pi.

Button connection:
- Button pins connected to GPIO (e.g., GPIO17, GPIO24, GPIO27)
- Other side of button connected to GND
- Internal pull-up resistor enabled (button pressed = LOW, released = HIGH)
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

# Try to import lgpio - will fail on non-Raspberry Pi systems
try:
    import lgpio
    GPIO_AVAILABLE = True
except ImportError:
    logger.warning("lgpio not available - GPIO functionality disabled")
    GPIO_AVAILABLE = False


@dataclass
class ButtonConfig:
    """Configuration for a GPIO button."""
    id: str
    button_number: int
    gpio_pin: int
    label: str


class GPIOButtonHandler:
    """
    Handles GPIO button presses on Raspberry Pi.

    Monitors configured GPIO pins and sends button press events to the backend.
    Uses polling with state change detection and debouncing.

    Button wiring:
    - Button connected between GPIO pin and GND
    - Internal pull-up resistor is enabled
    - LOW (0) = pressed, HIGH (1) = released
    """

    # Debounce time in milliseconds
    DEBOUNCE_MS = 200
    # Polling interval in seconds (50ms = 0.05s)
    POLL_INTERVAL = 0.05
    # GPIO chip number (0 for Raspberry Pi)
    GPIO_CHIP = 0

    def __init__(
        self,
        backend_url: str,
        device_id: str,
        device_token: str,
    ):
        self.backend_url = backend_url.rstrip('/')
        self.device_id = device_id
        self.device_token = device_token

        self.buttons: dict[int, ButtonConfig] = {}  # gpio_pin -> ButtonConfig
        self.last_press: dict[int, float] = {}  # gpio_pin -> timestamp (ms)
        self._running = False
        self._gpio_handle: int | None = None
        self._monitor_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> bool:
        """
        Start the GPIO button handler.

        Returns True if GPIO is available and initialized successfully.
        The handler starts even without buttons configured, and will begin
        monitoring when buttons are added via refresh_config().
        """
        if not GPIO_AVAILABLE:
            logger.info("GPIO not available - button handler disabled")
            return False

        # Initialize GPIO
        try:
            self._gpio_handle = lgpio.gpiochip_open(self.GPIO_CHIP)
            logger.info(f"GPIO chip opened (gpiochip{self.GPIO_CHIP}), handle: {self._gpio_handle}")
        except Exception as e:
            logger.error(f"Failed to open GPIO chip: {e}")
            return False

        # Create HTTP session
        self._session = aiohttp.ClientSession()

        # Fetch button configuration from backend
        await self._fetch_button_config()

        # Configure any existing buttons
        self._configure_gpio_pins(self.buttons.keys())

        # Start monitoring task (even if no buttons yet - they can be added later)
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_buttons())

        logger.info(f"GPIO button handler started with {len(self.buttons)} buttons")
        return True

    def _configure_gpio_pins(self, pins) -> None:
        """Configure GPIO pins as inputs with pull-up resistors."""
        if self._gpio_handle is None or not GPIO_AVAILABLE:
            return

        for gpio_pin in pins:
            button = self.buttons.get(gpio_pin)
            if not button:
                continue

            try:
                lgpio.gpio_claim_input(
                    self._gpio_handle,
                    gpio_pin,
                    lgpio.SET_PULL_UP  # Internal pull-up resistor
                )
                # Read initial state
                state = lgpio.gpio_read(self._gpio_handle, gpio_pin)
                state_str = "RELEASED (HIGH)" if state else "PRESSED (LOW)"
                logger.info(
                    f"Configured GPIO{gpio_pin} for button {button.button_number} "
                    f"({button.label or 'no label'}) - initial: {state_str}"
                )
            except Exception as e:
                logger.error(f"Failed to configure GPIO {gpio_pin}: {e}")

    async def stop(self) -> None:
        """Stop the GPIO button handler."""
        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.close()
            self._session = None

        if self._gpio_handle is not None and GPIO_AVAILABLE:
            try:
                lgpio.gpiochip_close(self._gpio_handle)
                logger.info("GPIO chip closed")
            except Exception as e:
                logger.error(f"Error closing GPIO chip: {e}")
            self._gpio_handle = None

        logger.info("GPIO button handler stopped")

    async def _fetch_button_config(self) -> None:
        """Fetch button configuration from backend."""
        url = f"{self.backend_url}/api/v1/device/buttons/"
        headers = self._get_auth_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        buttons = data.get("buttons", [])

                        self.buttons = {}
                        for btn in buttons:
                            config = ButtonConfig(
                                id=btn["id"],
                                button_number=btn["button_number"],
                                gpio_pin=btn["gpio_pin"],
                                label=btn.get("label", ""),
                            )
                            self.buttons[config.gpio_pin] = config

                        logger.info(f"Loaded {len(self.buttons)} button configurations")
                    else:
                        logger.error(f"Failed to fetch button config: {response.status}")
        except Exception as e:
            logger.error(f"Error fetching button config: {e}")

    async def refresh_config(self) -> None:
        """Refresh button configuration from backend."""
        old_pins = set(self.buttons.keys())
        await self._fetch_button_config()
        new_pins = set(self.buttons.keys())

        removed_pins = old_pins - new_pins
        added_pins = new_pins - old_pins

        if self._gpio_handle is not None and GPIO_AVAILABLE:
            # Release old pins that are no longer used
            for pin in removed_pins:
                try:
                    lgpio.gpio_free(self._gpio_handle, pin)
                    logger.info(f"Released GPIO {pin}")
                except Exception as e:
                    logger.error(f"Error releasing GPIO {pin}: {e}")

            # Configure new pins
            self._configure_gpio_pins(added_pins)

        if added_pins:
            logger.info(f"Added {len(added_pins)} new button(s): GPIO {list(added_pins)}")
        if removed_pins:
            logger.info(f"Removed {len(removed_pins)} button(s): GPIO {list(removed_pins)}")

    async def _monitor_buttons(self) -> None:
        """
        Monitor GPIO buttons for presses using polling.

        Uses state change detection with debouncing.
        Button pressed = LOW (0), released = HIGH (1) with pull-up resistor.
        """
        # Store previous states for edge detection
        prev_states: dict[int, int] = {}

        # Initialize states for all configured buttons
        for gpio_pin in self.buttons:
            if self._gpio_handle is not None:
                try:
                    prev_states[gpio_pin] = lgpio.gpio_read(self._gpio_handle, gpio_pin)
                except Exception:
                    prev_states[gpio_pin] = 1  # Assume released (HIGH)

        logger.info(f"Monitoring GPIO buttons (starting with {len(self.buttons)})...")

        while self._running:
            # Copy buttons dict to avoid issues if it's modified during iteration
            current_buttons = dict(self.buttons)

            for gpio_pin, button in current_buttons.items():
                if self._gpio_handle is None:
                    continue

                try:
                    # Read current state (0 = pressed, 1 = released with pull-up)
                    current_state = lgpio.gpio_read(self._gpio_handle, gpio_pin)
                    prev_state = prev_states.get(gpio_pin, 1)

                    # Detect state change
                    if current_state != prev_state:
                        if current_state == 0:
                            # Button pressed (falling edge: HIGH -> LOW)
                            now = time.time() * 1000  # milliseconds
                            last = self.last_press.get(gpio_pin, 0)

                            if now - last > self.DEBOUNCE_MS:
                                self.last_press[gpio_pin] = now
                                logger.info(
                                    f"Button {button.button_number} PRESSED "
                                    f"(GPIO{gpio_pin}, {button.label or 'no label'})"
                                )

                                # Send event to backend asynchronously
                                asyncio.create_task(
                                    self._send_button_press(button.button_number)
                                )
                        else:
                            # Button released (rising edge: LOW -> HIGH)
                            logger.debug(
                                f"Button {button.button_number} released (GPIO{gpio_pin})"
                            )

                        prev_states[gpio_pin] = current_state

                except Exception as e:
                    logger.error(f"Error reading GPIO{gpio_pin}: {e}")

            # Poll at configured interval
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _send_button_press(self, button_number: int) -> None:
        """Send button press event to backend."""
        url = f"{self.backend_url}/api/v1/device/buttons/{button_number}/press/"
        headers = self._get_auth_headers()

        try:
            if self._session:
                async with self._session.post(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        action = data.get("action")
                        if action:
                            logger.info(
                                f"Button {button_number} action: {action.get('type')} "
                                f"-> {action.get('court') or action.get('camera')}"
                            )
                        else:
                            logger.info(f"Button {button_number} pressed, no action configured")
                    else:
                        error = await response.text()
                        logger.error(f"Failed to send button press: {response.status} - {error}")
        except Exception as e:
            logger.error(f"Error sending button press event: {e}")

    def _get_auth_headers(self) -> dict:
        """Get authentication headers for backend API."""
        import base64
        credentials = f"{self.device_id}:{self.device_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
        }

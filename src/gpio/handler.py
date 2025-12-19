"""
GPIO Button Handler for Raspberry Pi.

This module handles physical GPIO button presses and sends events to the backend.
Uses lgpio library for GPIO control on Raspberry Pi.

Architecture:
- Device has 6 fixed GPIO pins mapped to button numbers 1-6
- When a button is pressed, device sends POST to backend with button number
- Backend decides what action to execute based on configuration

Button connection:
- Button pins connected to GPIO (e.g., GPIO17, GPIO24, GPIO27)
- Other side of button connected to GND
- Internal pull-up resistor enabled (button pressed = LOW, released = HIGH)
"""

import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger(__name__)

# Try to import lgpio - will fail on non-Raspberry Pi systems
try:
    import lgpio
    GPIO_AVAILABLE = True
except ImportError:
    logger.warning("lgpio not available - GPIO functionality disabled")
    GPIO_AVAILABLE = False


class GPIOButtonHandler:
    """
    Handles GPIO button presses on Raspberry Pi.

    Uses fixed GPIO pin to button number mapping. When a button is pressed,
    sends the button number to the backend which decides what action to take.

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

    # Fixed GPIO pin to button number mapping
    # These are the physical buttons on the device
    GPIO_TO_BUTTON = {
        17: 1,  # GPIO17 -> Button 1
        27: 2,  # GPIO27 -> Button 2
        24: 3,  # GPIO24 -> Button 3
        5: 4,   # GPIO5  -> Button 4
        16: 5,  # GPIO16 -> Button 5
        26: 6,  # GPIO26 -> Button 6
    }

    def __init__(
        self,
        backend_url: str,
        device_id: str,
        device_token: str,
    ):
        self.backend_url = backend_url.rstrip('/')
        self.device_id = device_id
        self.device_token = device_token

        self.last_press: dict[int, float] = {}  # gpio_pin -> timestamp (ms)
        self._running = False
        self._gpio_handle: int | None = None
        self._monitor_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._allocated_pins: set[int] = set()  # Pins successfully allocated

    @property
    def buttons(self) -> dict:
        """Return the fixed GPIO to button mapping for compatibility."""
        return self.GPIO_TO_BUTTON

    async def start(self) -> bool:
        """
        Start the GPIO button handler.

        Returns True if GPIO is available and initialized successfully.
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

        # Configure GPIO pins
        self._configure_gpio_pins()

        # Start monitoring task
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_buttons())

        logger.info(f"GPIO button handler started - {len(self._allocated_pins)} pins allocated")
        logger.info(f"Button mapping: {self.GPIO_TO_BUTTON}")
        return True

    def _configure_gpio_pins(self) -> None:
        """Configure GPIO pins as inputs with pull-up resistors."""
        if self._gpio_handle is None or not GPIO_AVAILABLE:
            return

        self._allocated_pins.clear()

        for gpio_pin in self.GPIO_TO_BUTTON.keys():
            try:
                lgpio.gpio_claim_input(
                    self._gpio_handle,
                    gpio_pin,
                    lgpio.SET_PULL_UP  # Internal pull-up resistor
                )
                # Read initial state
                state = lgpio.gpio_read(self._gpio_handle, gpio_pin)
                state_str = "RELEASED (HIGH)" if state else "PRESSED (LOW)"
                button_num = self.GPIO_TO_BUTTON[gpio_pin]
                logger.info(f"GPIO{gpio_pin} -> Button {button_num} - initial: {state_str}")
                # Track successfully allocated pin
                self._allocated_pins.add(gpio_pin)
            except Exception as e:
                logger.warning(f"GPIO{gpio_pin} not available: {e}")

        if self._allocated_pins:
            logger.info(f"Successfully allocated GPIO pins: {sorted(self._allocated_pins)}")
        else:
            logger.warning("No GPIO pins could be allocated")

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

    async def refresh_config(self) -> None:
        """
        Refresh configuration (no-op for fixed mapping).

        This method exists for API compatibility but does nothing
        since the GPIO to button mapping is fixed.
        """
        logger.info("Button config is fixed - no refresh needed")

    async def _monitor_buttons(self) -> None:
        """
        Monitor GPIO pins for button presses using polling.

        Uses state change detection with debouncing.
        Button pressed = LOW (0), released = HIGH (1) with pull-up resistor.
        """
        # Store previous states for edge detection
        prev_states: dict[int, int] = {}

        # Initialize states for allocated GPIO pins
        for gpio_pin in self._allocated_pins:
            if self._gpio_handle is not None:
                try:
                    prev_states[gpio_pin] = lgpio.gpio_read(self._gpio_handle, gpio_pin)
                except Exception:
                    prev_states[gpio_pin] = 1  # Assume released (HIGH)

        if not self._allocated_pins:
            logger.warning("No GPIO pins allocated - button monitoring disabled")
            return

        logger.info(f"Monitoring GPIO pins: {sorted(self._allocated_pins)}")

        while self._running:
            if self._gpio_handle is None:
                await asyncio.sleep(self.POLL_INTERVAL)
                continue

            for gpio_pin in self._allocated_pins:
                try:
                    # Read current state (0 = pressed, 1 = released with pull-up)
                    current_state = lgpio.gpio_read(self._gpio_handle, gpio_pin)
                    prev_state = prev_states.get(gpio_pin, 1)

                    # Detect state change
                    if current_state != prev_state:
                        button_number = self.GPIO_TO_BUTTON[gpio_pin]

                        if current_state == 0:
                            # Button pressed (falling edge: HIGH -> LOW)
                            now = time.time() * 1000  # milliseconds
                            last = self.last_press.get(gpio_pin, 0)

                            if now - last > self.DEBOUNCE_MS:
                                self.last_press[gpio_pin] = now
                                logger.info(f"GPIO{gpio_pin} PRESSED -> Button {button_number}")
                                # Send event to backend asynchronously
                                asyncio.create_task(
                                    self._send_button_press(button_number)
                                )
                        else:
                            # Button released (rising edge: LOW -> HIGH)
                            logger.debug(f"GPIO{gpio_pin} RELEASED -> Button {button_number}")

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
                            # Action is executed asynchronously by backend
                            action_type = action.get("type", "unknown")
                            court = action.get("court")
                            camera = action.get("camera")
                            target = court or camera or ""
                            logger.info(
                                f"Button {button_number} -> {action_type}"
                                f"{f' ({target})' if target else ''} - sent to backend"
                            )
                        else:
                            logger.info(f"Button {button_number} pressed - no action configured")
                    elif response.status == 404:
                        logger.info(f"Button {button_number} not configured in backend")
                    else:
                        error = await response.text()
                        logger.error(f"Button press failed: {response.status} - {error}")
        except Exception as e:
            logger.error(f"Error sending button press: {e}")

    def _get_auth_headers(self) -> dict:
        """Get authentication headers for backend API."""
        import base64
        credentials = f"{self.device_id}:{self.device_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
        }

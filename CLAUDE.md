# BeachVar Device

Raspberry Pi application for BeachVar - captures and uploads videos from IP cameras.

## Tech Stack

- **Language**: Python 3.11+
- **Package Manager**: UV
- **Video Capture**: FFmpeg (via ffmpeg-python)
- **GPIO**: RPi.GPIO ou gpiozero
- **Container**: Docker (ARM64)

## Code Standards

### Language

- **All code must be in English**: class names, function names, variables, comments, docstrings
- **Log messages in English**

```python
# CORRECT
class ButtonHandler:
    """Handles physical button press events."""

    def on_button_press(self, button_id: int):
        """Called when a button is pressed."""
        logger.info(f"Button {button_id} pressed")

# INCORRECT
class ManipuladorBotao:
    """Manipula eventos de botões físicos."""

    def ao_pressionar_botao(self, id_botao: int):
        logger.info(f"Botão {id_botao} pressionado")
```

### Project Structure

```
beachvar-device/
├── src/
│   ├── __init__.py
│   ├── main.py              # Entry point
│   ├── config.py            # Configuration from env
│   ├── hardware/
│   │   ├── __init__.py
│   │   ├── gpio_handler.py  # Button detection (interrupt-based)
│   │   └── led_indicator.py # Status LEDs
│   ├── camera/
│   │   ├── __init__.py
│   │   ├── rtsp_capture.py  # Single camera RTSP capture
│   │   └── multi_camera.py  # Parallel multi-camera capture
│   ├── api/
│   │   ├── __init__.py
│   │   ├── client.py        # HTTP client for backend
│   │   └── auth.py          # Token management
│   ├── upload/
│   │   ├── __init__.py
│   │   ├── transloadit.py   # Transloadit upload
│   │   └── queue.py         # Offline queue with retry
│   └── utils/
│       ├── __init__.py
│       └── logger.py
├── tests/
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

### Naming Conventions

- **Classes**: PascalCase (`ButtonHandler`, `CameraCapture`, `UploadQueue`)
- **Functions/Methods**: snake_case (`capture_video`, `send_to_backend`)
- **Constants**: UPPER_SNAKE_CASE (`BUTTON_1_GPIO`, `DEFAULT_DURATION`)
- **Files**: snake_case (`gpio_handler.py`, `rtsp_capture.py`)

### Type Hints

Always use type hints:

```python
from typing import Optional, List
from pathlib import Path

def capture_video(
    rtsp_url: str,
    duration_seconds: int,
    output_path: Path
) -> Optional[Path]:
    """Capture video from RTSP stream."""
    pass

def upload_files(files: List[Path], with_ads: bool = False) -> str:
    """Upload files to Transloadit and return assembly ID."""
    pass
```

### Error Handling

Use custom exceptions and proper logging:

```python
class CaptureError(Exception):
    """Raised when video capture fails."""
    pass

class UploadError(Exception):
    """Raised when upload to Transloadit fails."""
    pass

try:
    video_path = capture_video(rtsp_url, duration)
except CaptureError as e:
    logger.error(f"Failed to capture video: {e}")
    led_indicator.show_error()
```

### Configuration

Use environment variables via pydantic-settings:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    backend_url: str
    device_token: str
    button_1_gpio: int = 17
    button_2_gpio: int = 27
    led_status_gpio: int = 22
    led_error_gpio: int = 23

    class Config:
        env_file = ".env"
```

### GPIO Handling

- Use interrupt-based detection (not polling)
- Debounce buttons (hardware or software)
- Clean up GPIO on exit

```python
import RPi.GPIO as GPIO
import atexit

def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_1_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.add_event_detect(
        BUTTON_1_GPIO,
        GPIO.FALLING,
        callback=on_button_press,
        bouncetime=300
    )

atexit.register(GPIO.cleanup)
```

### Video Capture

- Use FFmpeg for RTSP capture
- Capture all cameras in parallel using ThreadPoolExecutor
- Handle connection failures gracefully

```python
from concurrent.futures import ThreadPoolExecutor

def capture_all_cameras(cameras: List[Camera], duration: int) -> List[Path]:
    with ThreadPoolExecutor(max_workers=len(cameras)) as executor:
        futures = [
            executor.submit(capture_video, cam.rtsp_url, duration)
            for cam in cameras
        ]
        return [f.result() for f in futures]
```

### Offline Queue

- Store failed uploads locally (SQLite)
- Retry when connection is restored
- Limit queue size to prevent disk full

## Commands

```bash
# Install dependencies
uv sync

# Run application
uv run python -m src.main

# Run with Docker
docker-compose up

# Run tests
uv run pytest

# Format code
uv run ruff format .

# Lint code
uv run ruff check .
```

## Environment Variables

```env
BACKEND_URL=https://api.beachvar.com
DEVICE_TOKEN=your-device-token-here

# GPIO pins (BCM numbering)
BUTTON_1_GPIO=17
BUTTON_2_GPIO=27
LED_STATUS_GPIO=22
LED_ERROR_GPIO=23

# Optional overrides
# RECORDING_DURATION_SECONDS=45
```

## Hardware Setup

### Buttons
- Button 1 (no ads): GPIO 17
- Button 2 (with ads): GPIO 27
- Both buttons use internal pull-up resistors

### LEDs
- Status LED (blue): GPIO 22
- Error LED (red): GPIO 23

### LED States
- Off: Idle
- Blinking slow: Recording
- Blinking fast: Uploading
- Solid: Success (5 seconds)
- Red: Error

## Key Features

### Two Buttons
- **Button 1**: Capture video WITHOUT sponsor ads
- **Button 2**: Capture video WITH sponsor overlay (scrolls right to left)

### Camera Support
- IP cameras with RTSP
- 1-6 cameras per court
- Parallel capture using ThreadPoolExecutor

### Notifications
- After upload completes, backend sends WhatsApp notification to all session participants

### Recording Duration
- Default: 45 seconds
- Configurable per complex (fetched from backend via `/api/v1/device/config/`)

## Related Documentation

- Full project plan: `ai-data/PLAN.md`

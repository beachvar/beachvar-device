"""
Sentry integration for BeachVar Device.

Provides error tracking and distributed tracing with device context.
"""

import functools
import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Global flag to track if Sentry is initialized
_sentry_initialized = False


def init_sentry(
    dsn: str,
    device_id: str,
    environment: str = "production",
    traces_sample_rate: float = 0.2,
    release: Optional[str] = None,
) -> bool:
    """
    Initialize Sentry SDK with device context.

    Args:
        dsn: Sentry DSN from backend config
        device_id: Unique device identifier (always included in events)
        environment: Environment name (production, staging, etc.)
        traces_sample_rate: Percentage of transactions to trace (0.0 to 1.0)
        release: Version/release identifier

    Returns:
        True if initialized successfully, False otherwise
    """
    global _sentry_initialized

    if not dsn:
        logger.info("Sentry DSN not provided, skipping initialization")
        return False

    if _sentry_initialized:
        logger.debug("Sentry already initialized")
        return True

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.aiohttp import AioHttpIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release or os.getenv("VERSION", "unknown"),

            # Integrations
            integrations=[
                # Capture ERROR logs automatically as events
                LoggingIntegration(
                    level=logging.INFO,         # Breadcrumbs from INFO+
                    event_level=logging.ERROR,  # Events from ERROR+
                ),
                # Auto-instrument aiohttp HTTP calls
                AioHttpIntegration(),
            ],

            # Tracing / Performance
            traces_sample_rate=traces_sample_rate,

            # Don't send PII
            send_default_pii=False,

            # Attach stacktrace to non-exception events
            attach_stacktrace=True,
        )

        # Set global tags that will be included in ALL events
        sentry_sdk.set_tag("device_id", device_id)
        sentry_sdk.set_tag("service", "beachvar-device")

        # Set user context with device_id for easy filtering
        sentry_sdk.set_user({"id": device_id})

        _sentry_initialized = True
        logger.info(f"Sentry initialized (env={environment}, traces={traces_sample_rate})")
        return True

    except ImportError:
        logger.warning("sentry-sdk not installed, skipping Sentry initialization")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")
        return False


def set_camera_context(camera_id: str, camera_name: str) -> None:
    """
    Set camera context for the current scope.

    Call this when processing camera-specific operations.
    """
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk

        sentry_sdk.set_tag("camera_id", camera_id)
        sentry_sdk.set_tag("camera_name", camera_name)
        sentry_sdk.set_context("camera", {
            "id": camera_id,
            "name": camera_name,
        })
    except Exception:
        pass


def clear_camera_context() -> None:
    """Clear camera context after processing."""
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk

        # Remove camera-specific tags
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("camera_id", None)
            scope.set_tag("camera_name", None)
            scope.set_context("camera", None)
    except Exception:
        pass


def capture_exception(exception: Exception, **extra_context) -> None:
    """
    Capture an exception with optional extra context.

    Args:
        exception: The exception to capture
        **extra_context: Additional context to attach
    """
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for key, value in extra_context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_exception(exception)
    except Exception:
        pass


def capture_message(message: str, level: str = "info", **extra_context) -> None:
    """
    Capture a message (non-exception event).

    Args:
        message: The message to capture
        level: Log level (debug, info, warning, error, fatal)
        **extra_context: Additional context to attach
    """
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for key, value in extra_context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_message(message, level=level)
    except Exception:
        pass


# =============================================================================
# Tracing Decorators
# =============================================================================


def traced(
    op: str = "function",
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Callable:
    """
    Decorator to create a Sentry transaction (trace) for an async function.

    Use this for top-level operations like start_stream, sync_device_state.

    Args:
        op: Operation type (e.g., "stream", "sync", "http")
        name: Transaction name (defaults to function name)
        description: Human-readable description

    Example:
        @traced(op="stream", name="start_stream")
        async def start_stream(self, camera_id: str) -> bool:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            if not _sentry_initialized:
                return await func(*args, **kwargs)

            try:
                import sentry_sdk

                transaction_name = name or func.__name__
                with sentry_sdk.start_transaction(
                    op=op,
                    name=transaction_name,
                    description=description,
                ) as transaction:
                    try:
                        result = await func(*args, **kwargs)
                        transaction.set_status("ok")
                        return result
                    except Exception as e:
                        transaction.set_status("internal_error")
                        sentry_sdk.capture_exception(e)
                        raise
            except ImportError:
                return await func(*args, **kwargs)

        return wrapper
    return decorator


def span(
    op: str = "task",
    description: Optional[str] = None,
) -> Callable:
    """
    Decorator to create a Sentry span for an async function.

    Use this for sub-operations within a traced function.

    Args:
        op: Operation type (e.g., "http", "db", "subprocess")
        description: Human-readable description (defaults to function name)

    Example:
        @span(op="http", description="fetch_camera")
        async def get_camera(self, camera_id: str) -> CameraConfig:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            if not _sentry_initialized:
                return await func(*args, **kwargs)

            try:
                import sentry_sdk

                span_desc = description or func.__name__
                with sentry_sdk.start_span(op=op, description=span_desc):
                    return await func(*args, **kwargs)
            except ImportError:
                return await func(*args, **kwargs)

        return wrapper
    return decorator


class TracingContext:
    """
    Context manager for manual span creation.

    Use when you need more control over span data.

    Example:
        with TracingContext(op="subprocess", description="start_ffmpeg") as span:
            process = subprocess.Popen(...)
            span.set_data("pid", process.pid)
    """

    def __init__(self, op: str, description: str):
        self.op = op
        self.description = description
        self._span = None

    def __enter__(self):
        if not _sentry_initialized:
            return self

        try:
            import sentry_sdk
            self._span = sentry_sdk.start_span(op=self.op, description=self.description)
            self._span.__enter__()
        except Exception:
            pass

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._span:
            try:
                self._span.__exit__(exc_type, exc_val, exc_tb)
            except Exception:
                pass
        return False

    def set_data(self, key: str, value: Any) -> None:
        """Set data on the span."""
        if self._span:
            try:
                self._span.set_data(key, value)
            except Exception:
                pass

    def set_tag(self, key: str, value: str) -> None:
        """Set tag on the span."""
        if self._span:
            try:
                self._span.set_tag(key, value)
            except Exception:
                pass

    def set_status(self, status: str) -> None:
        """Set status on the span (ok, internal_error, etc.)."""
        if self._span:
            try:
                self._span.set_status(status)
            except Exception:
                pass

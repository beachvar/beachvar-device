# Stage 1: Build dependencies (cached unless pyproject.toml changes)
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy only dependency file first (for layer caching)
COPY pyproject.toml .

# Create virtual environment and install dependencies
# This layer is cached unless pyproject.toml changes
RUN uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python -r pyproject.toml


# Stage 2: Final image
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    v4l-utils \
    usbutils \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install cloudflared (optional, for tunnel support)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
        CLOUDFLARED_ARCH="linux-arm64"; \
    elif [ "$ARCH" = "armhf" ] || [ "$ARCH" = "arm" ]; then \
        CLOUDFLARED_ARCH="linux-arm"; \
    else \
        CLOUDFLARED_ARCH="linux-amd64"; \
    fi && \
    curl -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-${CLOUDFLARED_ARCH}" \
    -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

# Copy virtual environment from builder (dependencies already installed)
COPY --from=builder /app/.venv /app/.venv

# Copy application code (changes frequently, so comes last)
COPY src/ src/
COPY main.py .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

# Run the device
CMD ["python", "main.py"]

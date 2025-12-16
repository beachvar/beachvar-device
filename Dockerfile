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
# Detect architecture and download appropriate binary
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

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency file first (for layer caching)
COPY pyproject.toml .

# Install dependencies (cached unless pyproject.toml changes)
RUN uv pip install --system -e .

# Copy application code (changes frequently, so comes last)
COPY src/ src/
COPY main.py .

# Environment variables
ENV PYTHONUNBUFFERED=1

# Run the device
CMD ["python", "main.py"]

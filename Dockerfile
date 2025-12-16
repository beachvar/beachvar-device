# Stage 1: Build dependencies (cached unless pyproject.toml/uv.lock change)
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (for layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies using uv sync (uses lockfile for reproducible builds)
# --frozen: fail if lockfile is out of date
# --no-install-project: don't install the project itself (we just need deps)
RUN uv sync --frozen --no-install-project


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
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Run the device using the venv python explicitly
CMD ["/app/.venv/bin/python", "main.py"]

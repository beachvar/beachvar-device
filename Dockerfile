FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    v4l-utils \
    usbutils \
    && rm -rf /var/lib/apt/lists/*

# Install cloudflared (optional, for tunnel support)
RUN curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/
COPY main.py .

# Install dependencies
RUN uv pip install --system -e .

# Environment variables
ENV PYTHONUNBUFFERED=1

# Run the device
CMD ["python", "main.py"]

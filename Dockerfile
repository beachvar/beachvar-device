FROM python:3.12-slim

WORKDIR /app

# Set timezone to Brazil
ENV TZ=America/Sao_Paulo

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    v4l-utils \
    usbutils \
    ffmpeg \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

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

CMD ["python", "main.py"]

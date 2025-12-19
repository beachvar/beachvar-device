# --- Build stage ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Install build dependencies for lgpio
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    swig \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Clone and compile lgpio (C library for Raspberry Pi GPIO)
RUN git clone --depth 1 https://github.com/joan2937/lg.git /tmp/lg && \
    cd /tmp/lg && \
    make && \
    make install && \
    ldconfig

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies in virtual environment
RUN uv sync --frozen --no-dev

# Install lgpio Python bindings in the venv
RUN uv pip install /tmp/lg/PY_LGPIO && \
    rm -rf /tmp/lg

# --- Final stage ---
FROM python:3.12-slim-bookworm

WORKDIR /app

# Set timezone to Brazil
ENV TZ=America/Sao_Paulo

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Copy lgpio C libraries from builder
COPY --from=builder /usr/local/lib/liblgpio.so.1 /usr/local/lib/
COPY --from=builder /usr/local/lib/librgpio.so.1 /usr/local/lib/
COPY --from=builder /usr/local/include/lgpio.h /usr/local/include/
COPY --from=builder /usr/local/include/rgpio.h /usr/local/include/

# Update shared library cache
RUN ldconfig

# Copy virtual environment from builder (includes lgpio Python bindings)
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY src/ src/
COPY main.py .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "main.py"]

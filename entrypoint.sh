#!/bin/bash
set -e

# Copy SSH key with correct permissions if it exists
if [ -f "/ssh/id_ed25519.mount" ]; then
    cp /ssh/id_ed25519.mount /ssh/id_ed25519
    chmod 600 /ssh/id_ed25519
    echo "SSH key configured with correct permissions"
fi

# Start cloudflared tunnel in background if token is provided
if [ -n "$TUNNEL_TOKEN" ]; then
    echo "Starting cloudflared tunnel..."

    # Create cloudflared config with ingress rules
    mkdir -p /root/.cloudflared
    cat > /root/.cloudflared/config.yml << EOF
tunnel: ${TUNNEL_TOKEN}
ingress:
  # Route /terminal/* directly to ttyd (port 7682)
  - hostname: ${TUNNEL_HOSTNAME:-"*"}
    path: /terminal.*
    service: http://localhost:7682
  # Route everything else to the main HTTP server (port 8080)
  - hostname: ${TUNNEL_HOSTNAME:-"*"}
    service: http://localhost:8080
  # Catch-all (required by cloudflared)
  - service: http_status:404
EOF

    # Start cloudflared with config file
    cloudflared tunnel --config /root/.cloudflared/config.yml run &
    CLOUDFLARED_PID=$!
    echo "cloudflared started with PID $CLOUDFLARED_PID"

    # Give cloudflared time to start
    sleep 2
fi

# Execute the main command
exec "$@"

#!/bin/bash
set -e

# Copy SSH key with correct permissions if it exists
if [ -f "/ssh/id_ed25519.mount" ]; then
    cp /ssh/id_ed25519.mount /ssh/id_ed25519
    chmod 600 /ssh/id_ed25519
    echo "SSH key configured with correct permissions"
fi

# Start cloudflared tunnel if token is provided
if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
    echo "Starting Cloudflare Tunnel..."

    # Create config directory
    mkdir -p /etc/cloudflared

    # Generate cloudflared config with multiple ingress rules
    # Terminal path goes directly to ttyd (port 7682)
    # Everything else goes to the main app (port 8080)
    cat > /etc/cloudflared/config.yml << EOF
tunnel: beachvar-device
ingress:
  # Terminal WebSocket - direct to ttyd for proper WebSocket handling
  - hostname: ${TUNNEL_HOSTNAME:-devices.beachvar.cainelli.xyz}
    path: /terminal.*
    service: http://localhost:7682
  # Main application
  - hostname: ${TUNNEL_HOSTNAME:-devices.beachvar.cainelli.xyz}
    service: http://localhost:8080
  # Catch-all (required by cloudflared)
  - service: http_status:404
EOF

    echo "Cloudflared config:"
    cat /etc/cloudflared/config.yml

    # Start cloudflared in background
    cloudflared tunnel --no-autoupdate run --token "$CLOUDFLARE_TUNNEL_TOKEN" &
    CLOUDFLARED_PID=$!
    echo "Cloudflared started with PID $CLOUDFLARED_PID"

    # Give cloudflared time to establish connection
    sleep 2
fi

# Execute the main command
exec "$@"

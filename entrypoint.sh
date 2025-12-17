#!/bin/bash
set -e

# Copy SSH key with correct permissions if it exists
if [ -f "/ssh/id_ed25519.mount" ]; then
    cp /ssh/id_ed25519.mount /ssh/id_ed25519
    chmod 600 /ssh/id_ed25519
    echo "SSH key configured with correct permissions"
fi

# Execute the main command
exec "$@"

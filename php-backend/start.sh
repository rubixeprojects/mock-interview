#!/usr/bin/env bash
# Start the PHP mock-interview backend detached on 0.0.0.0:8080.
set -u
cd "$(dirname "$0")"
pkill -f "php -S 0.0.0.0:8080" 2>/dev/null || true
sleep 1
setsid php -S 0.0.0.0:8080 router.php > server.log 2>&1 < /dev/null &
disown 2>/dev/null || true
echo "started (detached); logs -> $(pwd)/server.log"

#!/bin/bash
# Ensure TradingView Desktop is running with CDP debug port
# Used by the pre-market briefing cron job

PORT="${1:-9222}"

# Check if CDP is already available
if curl -s "http://localhost:$PORT/json/version" > /dev/null 2>&1; then
  echo "TV_READY=true"
  echo "TV_PORT=$PORT"
  exit 0
fi

# If not running, launch it
APP="/Applications/TradingView.app/Contents/MacOS/TradingView"
if [ ! -f "$APP" ]; then
  echo "TV_READY=false"
  echo "TV_ERROR=TradingView not found at $APP"
  exit 1
fi

echo "Launching TradingView with --remote-debugging-port=$PORT ..."
"$APP" --remote-debugging-port=$PORT &
TV_PID=$!

# Wait up to 30s for CDP to be ready
for i in $(seq 1 30); do
  if curl -s "http://localhost:$PORT/json/version" > /dev/null 2>&1; then
    echo "TV_READY=true"
    echo "TV_PORT=$PORT"
    echo "TV_PID=$TV_PID"
    exit 0
  fi
  sleep 1
done

echo "TV_READY=false"
echo "TV_ERROR=CDP not ready after 30s"
exit 1

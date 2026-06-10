#!/bin/bash
# Pre-market data collection script
# Ensures TradingView is running and collects market data snapshot

PORT="9222"

# Ensure TradingView is running with debug port
bash /Users/jiayanghan/.hermes/scripts/ensure_tv_running.sh "$PORT"
TV_OK=$?

if [ $TV_OK -ne 0 ]; then
  echo "{\"status\": \"error\", \"message\": \"TradingView could not be started\"}"
  exit 1
fi

echo "{\"status\": \"ready\", \"port\": $PORT}"
exit 0

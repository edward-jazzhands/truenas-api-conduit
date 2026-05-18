#!/bin/bash
set -e

source .env

STATS_OUTPUT="/tmp/server-stats.jsonl"
UPTIME_OUTPUT="/tmp/uptime.txt"
PIPE="/tmp/truenas-ws.pipe"

# Clean up pipe and background jobs on exit
cleanup() {
    rm -f "$PIPE"
    kill 0
}
trap cleanup EXIT

# Create the named pipe
mkfifo "$PIPE"

# Open persistent websocket connection, feeding from the pipe
# tail -f keeps the pipe open so websocat doesn't exit when we're between writes
tail -f "$PIPE" | websocat -t "wss://$TRUENAS_HOST/api/current" | while IFS= read -r line; do
    echo "$line" >> "$STATS_OUTPUT"
done &

sleep 1  # Give the connection a moment to establish

# Authenticate once
echo '{"id":1,"jsonrpc":"2.0","method":"auth.login_with_api_key","params":["'"$TRUENAS_API_KEY"'"]}' > "$PIPE"
sleep 2  # Wait for auth to complete

REQ_ID=2

while true; do
    echo '{"id":'"$REQ_ID"',"jsonrpc":"2.0","method":"system.info","params":[]}' > "$PIPE"

    sleep 3  # Give the response time to arrive

    # Pull the response for this specific request ID
    jq -c 'select(.id == '"$REQ_ID"')' "$STATS_OUTPUT" | tail -1 | jq '.result.uptime' > "$UPTIME_OUTPUT"

    # Increment ID so responses stay identifiable
    REQ_ID=$((REQ_ID + 1))

    sleep 10
done
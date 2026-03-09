#!/usr/bin/env bash
# Corporate AI Dashboard - Start Script (Linux/macOS)
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS_FILE="$ROOT/.pids"
LOG_DIR="$ROOT/logs"

echo ""
echo "  Corporate AI Dashboard - Starting Up"
echo ""

# Prerequisites
[ -f "$ROOT/main.py" ]              || { echo "[ERROR] main.py not found. Run from corporate-ai folder."; exit 1; }
[ -f "$ROOT/relay/server.js" ]      || { echo "[ERROR] relay/server.js not found.";      exit 1; }
[ -f "$ROOT/dashboard/index.html" ] || { echo "[ERROR] dashboard/index.html not found."; exit 1; }

# Port configuration
echo "  Configure ports (press Enter to accept defaults):"
echo ""

read_port() {
    local label="$1" default="$2" val
    read -rp "    $label port [$default]: " val
    val="${val:-$default}"
    if ! [[ "$val" =~ ^[0-9]+$ ]]; then
        echo "    Invalid port '$val', using default $default" >&2
        val="$default"
    fi
    echo "$val"
}

PORT_BACKEND=$(read_port   "Backend  " "8000")
PORT_RELAY=$(read_port     "Relay    " "3001")
PORT_DASHBOARD=$(read_port "Dashboard" "3000")
echo ""

# Stop any existing services
if [ -f "$PIDS_FILE" ]; then
    echo "  Stopping existing services first..."
    bash "$ROOT/server_stop.sh" --quiet || true
    sleep 1
fi

# Create logs dir; clear pids file
mkdir -p "$LOG_DIR"
> "$PIDS_FILE"

# Install relay deps if absent
if [ ! -d "$ROOT/relay/node_modules" ]; then
    echo "  Installing relay dependencies..."
    (cd "$ROOT/relay" && npm install)
    echo ""
fi

# [1/3] Backend — PORT env var sets the uvicorn listen port
echo "  [1/3] Starting backend  (port $PORT_BACKEND)..."
(cd "$ROOT" && PORT=$PORT_BACKEND python main.py \
    > "$LOG_DIR/backend.log" 2> "$LOG_DIR/backend.err") &
echo "backend=$!" >> "$PIDS_FILE"
echo "         PID $! -> logs/backend.log"
sleep 2

# [2/3] Relay — RELAY_PORT sets listen port; CA_PORT tells relay where backend is
echo "  [2/3] Starting relay    (port $PORT_RELAY)..."
(RELAY_PORT=$PORT_RELAY CA_PORT=$PORT_BACKEND \
    node "$ROOT/relay/server.js" \
    > "$LOG_DIR/relay.log" 2> "$LOG_DIR/relay.err") &
echo "relay=$!" >> "$PIDS_FILE"
echo "         PID $! -> logs/relay.log"
sleep 2

# [3/3] Dashboard
echo "  [3/3] Starting dashboard (port $PORT_DASHBOARD)..."
(cd "$ROOT/dashboard" && npx serve . -p "$PORT_DASHBOARD" \
    > "$LOG_DIR/dashboard.log" 2> "$LOG_DIR/dashboard.err") &
echo "dashboard=$!" >> "$PIDS_FILE"
echo "         PID $! -> logs/dashboard.log"

echo ""
echo "  All services started. PIDs saved to .pids"
echo ""
echo "  Backend:   http://localhost:$PORT_BACKEND"
echo "  Relay:     http://localhost:$PORT_RELAY"
echo "  Dashboard: http://localhost:$PORT_DASHBOARD"
echo ""
echo "  Open http://localhost:$PORT_DASHBOARD in your browser."
echo "  Run ./server_stop.sh to stop all services."
echo ""

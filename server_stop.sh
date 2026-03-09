#!/usr/bin/env bash
# Corporate AI Dashboard — Stop Script (Linux/macOS)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS_FILE="$ROOT/.pids"
QUIET="${1:-}"

[ "$QUIET" != "--quiet" ] && {
    echo ""
    echo "  Corporate AI Dashboard - Stopping..."
    echo ""
}

if [ ! -f "$PIDS_FILE" ]; then
    [ "$QUIET" != "--quiet" ] && echo "  .pids not found — nothing to stop."
    exit 0
fi

while IFS='=' read -r name pid; do
    [ -z "$name" ] || [ -z "$pid" ] && continue
    # Trim whitespace
    name="$(echo "$name" | xargs)"
    pid="$(echo "$pid" | xargs)"
    # Validate pid is numeric
    [[ "$pid" =~ ^[0-9]+$ ]] || continue

    if kill -0 "$pid" 2>/dev/null; then
        [ "$QUIET" != "--quiet" ] && echo "  Stopping $name (PID $pid)..."
        # Kill process group to catch children spawned by subshell
        kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    else
        [ "$QUIET" != "--quiet" ] && echo "  $name (PID $pid) already stopped."
    fi
done < "$PIDS_FILE"

rm -f "$PIDS_FILE"

[ "$QUIET" != "--quiet" ] && {
    echo ""
    echo "  Done."
    echo ""
}

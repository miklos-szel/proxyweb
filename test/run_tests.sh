#!/usr/bin/env bash
# Run the full integration test suite against a fresh docker-compose stack.
# The stack is torn down automatically afterwards (pass --keep to leave it up).
#
# Service endpoints and credentials used by this stack:
#
#   ProxyWeb UI        http://localhost:5000   admin / admin42
#
#   ProxySQL 1 admin   localhost:6032          radmin / radmin
#   ProxySQL 1 MySQL   localhost:6033          proxyuser / proxypass
#   MySQL 1 backend    (internal only)         root / rootpass  (db: testdb)
#
#   ProxySQL 2 admin   localhost:6034          radmin / radmin
#   ProxySQL 2 MySQL   localhost:6035          proxyuser2 / proxypass2
#   MySQL 2 backend    (internal only)         root / rootpass  (db: testdb2)
#
# Environment overrides (exported before calling this script):
#   PROXYWEB_URL   default: http://localhost:5000
#   PROXYWEB_USER  default: admin
#   PROXYWEB_PASS  default: admin42

set -euo pipefail
cd "$(dirname "$0")"

KEEP=0
for arg in "$@"; do
    [[ "$arg" == "--keep" ]] && KEEP=1
done

# ---------------------------------------------------------------------------
# Log setup
# ---------------------------------------------------------------------------

LOG_DIR="log"
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name "failure_*.log" -mtime +30 -delete 2>/dev/null || true

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/failure_${TIMESTAMP}.log"
TMPOUT=$(mktemp)
trap 'rm -f "$TMPOUT"' EXIT

# ---------------------------------------------------------------------------
# Cleanup / teardown trap
# ---------------------------------------------------------------------------

TEST_EXIT=0

# on_exit removes the temporary output file and either tears down the docker-compose stack or, when the --keep flag is set, prints stop instructions and the service endpoints/credentials.
on_exit() {
    rm -f "$TMPOUT"

    if [[ $KEEP -eq 0 ]]; then
        echo ""
        echo "==> Tearing down stack..."
        docker compose down --volumes --remove-orphans
    else
        echo ""
        echo "==> Stack left running (--keep). Stop with: docker compose down -v --remove-orphans"
        echo ""
        echo "    ProxyWeb UI      http://localhost:5000   admin / admin42"
        echo "    ProxySQL 1 admin localhost:6032          radmin / radmin"
        echo "    ProxySQL 1 MySQL localhost:6033          proxyuser / proxypass"
        echo "    ProxySQL 2 admin localhost:6034          radmin / radmin"
        echo "    ProxySQL 2 MySQL localhost:6035          proxyuser2 / proxypass2"
    fi
}
trap on_exit EXIT

# ---------------------------------------------------------------------------
# Stack + dependencies
# ---------------------------------------------------------------------------

echo "==> Building and starting stack (this may take a few minutes on first run)..."
docker compose up -d --build --wait

echo ""
echo "==> Installing test dependencies..."
#apt-get install -y -qq python3-requests python3-pymysql

# ---------------------------------------------------------------------------
# Run tests — output goes to terminal; captured separately for error logging
# ---------------------------------------------------------------------------

echo ""
echo "==> Running tests against http://localhost:5000 (admin / admin42)..."
echo ""

set +e
python3 test_proxyweb.py 2>&1 | tee "$TMPOUT"
TEST_EXIT=${PIPESTATUS[0]}
set -e

# ---------------------------------------------------------------------------
# On failure: write a focused error log with tracebacks + service diagnostics
# ---------------------------------------------------------------------------

if [[ $TEST_EXIT -ne 0 ]]; then
    {
        echo "=== PROXYWEB TEST FAILURE REPORT ==="
        echo "timestamp:  $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        echo "git_commit: $(git -C .. rev-parse --short HEAD 2>/dev/null || echo unknown)"
        echo "git_branch: $(git -C .. rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
        echo "host:       $(hostname)"
        echo "====================================="

        echo ""
        echo "=== FAILED / ERROR TESTS ==="
        # Extract the detailed failure blocks (everything from the first ====
        # separator through to the final summary line).
        awk '/^={10}/{found=1} found{print}' "$TMPOUT"

        echo ""
        echo "=== DOCKER SERVICE STATUS ==="
        docker compose ps --all 2>&1 || true

        echo ""
        echo "=== SERVICE LOGS ==="
        for svc in proxyweb proxysql proxysql2 mysql mysql2 proxysql-init proxysql2-init; do
            local_logs=$(docker compose logs --no-color --tail=200 "$svc" 2>&1 || true)
            # Only include a service's logs if they contain an error indicator.
            if echo "$local_logs" | grep -qiE 'error|exception|traceback|fatal|critical'; then
                echo ""
                echo "--- $svc (errors only — last 200 lines filtered) ---"
                echo "$local_logs" | grep -iE 'error|exception|traceback|fatal|critical|^\s+File |^\s+raise |^\w.*Error:'
            fi
        done

    } > "$LOG_FILE"

    echo ""
    echo "==> FAILED. Error log written to: $LOG_FILE"
else
    echo ""
    echo "==> All tests passed."
fi

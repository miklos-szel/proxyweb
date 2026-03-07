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
# Failure log setup
# ---------------------------------------------------------------------------

LOG_DIR="log"
mkdir -p "$LOG_DIR"

# Remove log files older than 30 days to keep the directory tidy
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/run_${TIMESTAMP}.log"

# Write a structured header that Claude can parse
{
    echo "=== PROXYWEB TEST RUN ==="
    echo "timestamp:   $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "git_commit:  $(git -C .. rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "git_branch:  $(git -C .. rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    echo "host:        $(hostname)"
    echo "========================="
} >> "$LOG_FILE"

# ---------------------------------------------------------------------------
# Cleanup trap
# ---------------------------------------------------------------------------

TEST_EXIT=0

cleanup() {
    local exit_code=$TEST_EXIT

    if [[ $exit_code -ne 0 ]]; then
        echo ""
        echo "==> Tests FAILED (exit $exit_code) — collecting service logs into $LOG_FILE ..."
        {
            echo ""
            echo "=== DOCKER SERVICE LOGS ==="
            for svc in proxyweb proxysql proxysql2 mysql mysql2 proxysql-init proxysql2-init; do
                echo ""
                echo "--- service: $svc ---"
                sudo docker compose logs --no-color --tail=200 "$svc" 2>&1 || echo "(service not found or no logs)"
            done
            echo ""
            echo "=== DOCKER SERVICE STATUS ==="
            sudo docker compose ps --all 2>&1 || true
        } >> "$LOG_FILE"
        echo "    Full log: $LOG_FILE"
    else
        echo ""
        echo "==> All tests passed. Log: $LOG_FILE"
    fi

    if [[ $KEEP -eq 0 ]]; then
        echo ""
        echo "==> Tearing down stack..."
        sudo docker compose down --volumes --remove-orphans
    else
        echo ""
        echo "==> Stack left running (--keep). Stop with: sudo docker compose down -v --remove-orphans"
        echo ""
        echo "    ProxyWeb UI      http://localhost:5000   admin / admin42"
        echo "    ProxySQL 1 admin localhost:6032          radmin / radmin"
        echo "    ProxySQL 1 MySQL localhost:6033          proxyuser / proxypass"
        echo "    ProxySQL 2 admin localhost:6034          radmin / radmin"
        echo "    ProxySQL 2 MySQL localhost:6035          proxyuser2 / proxypass2"
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Stack + dependencies
# ---------------------------------------------------------------------------

echo "==> Building and starting stack (this may take a few minutes on first run)..."
sudo docker compose up -d --build --wait

echo ""
echo "==> Installing test dependencies..."
sudo apt-get install -y -qq python3-requests python3-pymysql

# ---------------------------------------------------------------------------
# Run tests — capture output to log AND terminal simultaneously
# ---------------------------------------------------------------------------

echo ""
echo "==> Running tests against http://localhost:5000 (admin / admin42)..."
echo "    (output also logged to $LOG_FILE)"
echo ""

{
    echo ""
    echo "=== TEST OUTPUT ==="
} >> "$LOG_FILE"

# tee duplicates output: terminal + log file.
# We run python3 in a subshell so we can capture its exit code separately
# from the outer set -e (which would abort before we log service diagnostics).
set +e
python3 test_proxyweb.py 2>&1 | tee -a "$LOG_FILE"
TEST_EXIT=${PIPESTATUS[0]}
set -e

{
    echo ""
    echo "=== TEST EXIT CODE: $TEST_EXIT ==="
} >> "$LOG_FILE"

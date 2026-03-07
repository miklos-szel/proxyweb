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

cleanup() {
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

echo "==> Building and starting stack (this may take a few minutes on first run)..."
sudo docker compose up -d --build --wait

echo ""
echo "==> Installing test dependencies..."
sudo apt-get install -y -qq python3-requests

echo ""
echo "==> Running tests against http://localhost:5000 (admin / admin42)..."
python3 test_proxyweb.py

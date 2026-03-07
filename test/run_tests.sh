#!/usr/bin/env bash
# Run the full integration test suite against a fresh docker-compose stack.
# The stack is torn down automatically afterwards (pass --keep to leave it up).

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
        docker compose down --volumes --remove-orphans
    else
        echo ""
        echo "==> Stack left running (--keep). Stop with: docker compose down -v"
    fi
}
trap cleanup EXIT

echo "==> Building and starting stack (this may take a few minutes on first run)..."
docker compose up -d --build --wait

echo ""
echo "==> Installing test dependencies..."
pip3 install -q -r requirements.txt

echo ""
echo "==> Running tests..."
python3 test_proxyweb.py

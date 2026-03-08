#!/usr/bin/env bash
# Configure mysql3 as a replica of mysql2.
# Runs after both instances are healthy; reads the current master binary log
# position from mysql2 so only future writes (not the already-applied init
# data) are replicated to mysql3.

set -euo pipefail

MYSQL2="mysql -h mysql2 -u root -prootpass"
MYSQL3="mysql -h mysql3 -u root -prootpass"

echo "==> Fetching master status from mysql2..."
MASTER_FILE=$($MYSQL2 -e "SHOW MASTER STATUS\G" 2>/dev/null | awk '/File:/{print $2}')
MASTER_POS=$( $MYSQL2 -e "SHOW MASTER STATUS\G" 2>/dev/null | awk '/Position:/{print $2}')

if [[ -z "$MASTER_FILE" || -z "$MASTER_POS" ]]; then
    echo "ERROR: could not read master status from mysql2 (binary logging may be off)"
    exit 1
fi

echo "    File=$MASTER_FILE  Position=$MASTER_POS"

echo "==> Configuring mysql3 as replica..."
$MYSQL3 << EOF
STOP SLAVE;
RESET SLAVE ALL;
CHANGE MASTER TO
    MASTER_HOST='mysql2',
    MASTER_USER='replicator',
    MASTER_PASSWORD='replicapass',
    MASTER_LOG_FILE='$MASTER_FILE',
    MASTER_LOG_POS=$MASTER_POS;
START SLAVE;
EOF

echo "==> Waiting for replica to connect..."
for i in $(seq 1 20); do
    STATUS=$($MYSQL3 -e "SHOW SLAVE STATUS\G" 2>/dev/null)
    IO_RUNNING=$(echo "$STATUS" | awk '/Slave_IO_Running:/{print $2}')
    SQL_RUNNING=$(echo "$STATUS" | awk '/Slave_SQL_Running:/{print $2}')
    if [[ "$IO_RUNNING" == "Yes" && "$SQL_RUNNING" == "Yes" ]]; then
        echo "==> Replication running (IO: $IO_RUNNING, SQL: $SQL_RUNNING)"
        exit 0
    fi
    echo "    attempt $i: IO=$IO_RUNNING SQL=$SQL_RUNNING — waiting..."
    sleep 2
done

echo "ERROR: replication did not start within timeout"
$MYSQL3 -e "SHOW SLAVE STATUS\G" 2>/dev/null
exit 1

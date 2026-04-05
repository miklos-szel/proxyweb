-- Register PostgreSQL backends in proxysql.
-- hostgroup 10 = postgres backends

DELETE FROM pgsql_servers WHERE hostname IN ('postgres','postgres2') AND port=5432;
INSERT INTO pgsql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (10, 'postgres', 5432, 'ONLINE', 1, 200);
INSERT INTO pgsql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (10, 'postgres2', 5432, 'ONLINE', 1, 200);

-- Register the PostgreSQL application user.
DELETE FROM pgsql_users WHERE username='pguser';
INSERT INTO pgsql_users (username, password, default_hostgroup, max_connections, active)
VALUES ('pguser', 'pgpass', 10, 200, 1);

-- Activate and persist.
LOAD PGSQL SERVERS TO RUNTIME;
LOAD PGSQL USERS TO RUNTIME;
SAVE PGSQL SERVERS TO DISK;
SAVE PGSQL USERS TO DISK;

-- Register mysql2 (writer + reader) and mysql3 (reader only) in proxysql2.
-- hostgroup 1 = writer, hostgroup 2 = reader
DELETE FROM mysql_servers WHERE hostname IN ('mysql2','mysql3') AND port=3306;
INSERT INTO mysql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (1, 'mysql2', 3306, 'ONLINE', 1, 200);
INSERT INTO mysql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (2, 'mysql2', 3306, 'ONLINE', 1, 200);
INSERT INTO mysql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (2, 'mysql3', 3306, 'ONLINE', 1, 200);

-- Register the application user for the second instance.
DELETE FROM mysql_users WHERE username='proxyuser2';
INSERT INTO mysql_users (username, password, default_hostgroup, max_connections, default_schema, active)
VALUES ('proxyuser2', 'proxypass2', 1, 200, 'testdb2', 1);

-- Query rules: read/write split.
-- Rule 1: SELECT ... FOR UPDATE stays on writer (hg 1)
DELETE FROM mysql_query_rules WHERE rule_id=1;
INSERT INTO mysql_query_rules (rule_id, active, match_digest, destination_hostgroup, apply)
VALUES (1, 1, '^SELECT.*FOR UPDATE', 1, 1);

-- Rule 2: all other SELECTs go to reader (hg 2)
DELETE FROM mysql_query_rules WHERE rule_id=2;
INSERT INTO mysql_query_rules (rule_id, active, match_digest, destination_hostgroup, apply)
VALUES (2, 1, '^SELECT', 2, 1);

-- Configure replication hostgroups for read/write split awareness.
DELETE FROM mysql_replication_hostgroups WHERE writer_hostgroup=1;
INSERT INTO mysql_replication_hostgroups (writer_hostgroup, reader_hostgroup, comment)
VALUES (1, 2, 'read/write split');

-- Activate everything at runtime and persist to disk.
LOAD MYSQL SERVERS TO RUNTIME;
LOAD MYSQL USERS TO RUNTIME;
LOAD MYSQL QUERY RULES TO RUNTIME;
SAVE MYSQL SERVERS TO DISK;
SAVE MYSQL USERS TO DISK;
SAVE MYSQL QUERY RULES TO DISK;

-- Register the second MySQL backend server in ProxySQL2.
DELETE FROM mysql_servers WHERE hostname='mysql2' AND port=3306;
INSERT INTO mysql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (0, 'mysql2', 3306, 'ONLINE', 1, 200);

-- Register the application user for the second instance.
DELETE FROM mysql_users WHERE username='proxyuser2';
INSERT INTO mysql_users (username, password, default_hostgroup, max_connections, default_schema, active)
VALUES ('proxyuser2', 'proxypass2', 0, 200, 'testdb2', 1);

-- Add distinctive query rules so tests can tell the two ProxySQL instances apart.
-- Rule 1: route SELECT ... FOR UPDATE to writer hostgroup (hg 0)
DELETE FROM mysql_query_rules WHERE rule_id=1;
INSERT INTO mysql_query_rules (rule_id, active, match_digest, destination_hostgroup, apply)
VALUES (1, 1, '^SELECT.*FOR UPDATE', 0, 1);

-- Rule 2: route all other SELECTs to reader hostgroup (hg 1) — signals read/write split intent
DELETE FROM mysql_query_rules WHERE rule_id=2;
INSERT INTO mysql_query_rules (rule_id, active, match_digest, destination_hostgroup, apply)
VALUES (2, 1, '^SELECT', 1, 1);

-- Activate everything at runtime and persist to disk.
LOAD MYSQL SERVERS TO RUNTIME;
LOAD MYSQL USERS TO RUNTIME;
LOAD MYSQL QUERY RULES TO RUNTIME;
SAVE MYSQL SERVERS TO DISK;
SAVE MYSQL USERS TO DISK;
SAVE MYSQL QUERY RULES TO DISK;

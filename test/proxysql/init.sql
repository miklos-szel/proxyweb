-- Explicitly register the MySQL backend server in ProxySQL.
-- ProxySQL already loads these from proxysql.cnf on first start, but this
-- init service makes the state explicit and ensures LOAD TO RUNTIME is run.

DELETE FROM mysql_servers WHERE hostname='mysql' AND port=3306;
INSERT INTO mysql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (0, 'mysql', 3306, 'ONLINE', 1, 200);

-- Register the application test user.
DELETE FROM mysql_users WHERE username='proxyuser';
INSERT INTO mysql_users (username, password, default_hostgroup, max_connections, default_schema, active)
VALUES ('proxyuser', 'proxypass', 0, 200, 'testdb', 1);

-- Activate everything at runtime and persist to disk.
LOAD MYSQL SERVERS TO RUNTIME;
LOAD MYSQL USERS TO RUNTIME;
SAVE MYSQL SERVERS TO DISK;
SAVE MYSQL USERS TO DISK;

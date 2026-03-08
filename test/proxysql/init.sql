-- Explicitly register the MySQL backend server in ProxySQL.
-- ProxySQL already loads these from proxysql.cnf on first start, but this
-- init service makes the state explicit and ensures LOAD TO RUNTIME is run.

DELETE FROM mysql_servers WHERE hostname='mysql' AND port=3306;
INSERT INTO mysql_servers (hostgroup_id, hostname, port, status, weight, max_connections)
VALUES (1, 'mysql', 3306, 'ONLINE', 1, 200);

-- Register the application test user.
DELETE FROM mysql_users WHERE username='proxyuser';
INSERT INTO mysql_users (username, password, default_hostgroup, max_connections, default_schema, active)
VALUES ('proxyuser', 'proxypass', 1, 200, 'testdb', 1);

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

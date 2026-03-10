-- ProxySQL monitor user (used by ProxySQL to health-check backend servers)
CREATE USER 'monitor'@'%' IDENTIFIED WITH mysql_native_password BY 'monitor';
GRANT USAGE ON *.* TO 'monitor'@'%';

-- Application user (registered in ProxySQL mysql_users, routes queries through proxy)
CREATE USER 'proxyuser'@'%' IDENTIFIED WITH mysql_native_password BY 'proxypass';
GRANT ALL PRIVILEGES ON testdb.* TO 'proxyuser'@'%';
GRANT SELECT ON performance_schema.* TO 'proxyuser'@'%';

FLUSH PRIVILEGES;

-- Seed a small test table so there is real data to browse
USE testdb;
CREATE TABLE IF NOT EXISTS items (
    id   INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    val  INT          NOT NULL DEFAULT 0
);
INSERT INTO items (name, val) VALUES ('alpha', 1), ('beta', 2), ('gamma', 3);

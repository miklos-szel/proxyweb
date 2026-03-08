-- ProxySQL monitor user (used by ProxySQL to health-check backend servers)
CREATE USER 'monitor'@'%' IDENTIFIED WITH mysql_native_password BY 'monitor';
GRANT USAGE ON *.* TO 'monitor'@'%';

-- Application user for second ProxySQL instance
CREATE USER 'proxyuser2'@'%' IDENTIFIED WITH mysql_native_password BY 'proxypass2';
GRANT ALL PRIVILEGES ON testdb2.* TO 'proxyuser2'@'%';
GRANT SELECT ON performance_schema.* TO 'proxyuser2'@'%';

-- Replication user (used by mysql3 replica to connect to this master)
CREATE USER 'replicator'@'%' IDENTIFIED WITH mysql_native_password BY 'replicapass';
GRANT REPLICATION SLAVE ON *.* TO 'replicator'@'%';

FLUSH PRIVILEGES;

-- Seed a distinct test table in the second database
USE testdb2;
CREATE TABLE IF NOT EXISTS products (
    id    INT AUTO_INCREMENT PRIMARY KEY,
    name  VARCHAR(100) NOT NULL,
    price DECIMAL(10,2) NOT NULL DEFAULT 0.00
);
INSERT INTO products (name, price) VALUES ('widget', 9.99), ('gadget', 19.99), ('thingamajig', 4.99);

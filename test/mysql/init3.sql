-- ProxySQL monitor user
CREATE USER 'monitor'@'%' IDENTIFIED WITH mysql_native_password BY 'monitor';
GRANT USAGE ON *.* TO 'monitor'@'%';

-- Same application user as mysql2 so proxyuser2 can query via this reader
CREATE USER 'proxyuser2'@'%' IDENTIFIED WITH mysql_native_password BY 'proxypass2';
GRANT ALL PRIVILEGES ON testdb2.* TO 'proxyuser2'@'%';
GRANT SELECT ON performance_schema.* TO 'proxyuser2'@'%';

FLUSH PRIVILEGES;

-- Mirror the testdb2 schema and seed data for reader queries
USE testdb2;
CREATE TABLE IF NOT EXISTS products (
    id    INT AUTO_INCREMENT PRIMARY KEY,
    name  VARCHAR(100) NOT NULL,
    price DECIMAL(10,2) NOT NULL DEFAULT 0.00
);
INSERT INTO products (name, price) VALUES ('widget', 9.99), ('gadget', 19.99), ('thingamajig', 4.99);

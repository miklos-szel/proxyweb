# ProxyWeb Integration Tests

This directory contains a Docker Compose stack and a Python integration test
suite that exercise ProxyWeb against a real ProxySQL + MySQL backend.

## Stack layout

```
                          ┌──────────────┐     3306    ┌───────────┐
               admin ──►  │   proxysql   │ ──────────► │   mysql   │
               (6032)     │  sql (6033)  │             │  testdb   │
┌─────────────┐           └──────────────┘             └───────────┘
│   proxyweb  │
│  :5000      │           ┌──────────────┐     3306    ┌───────────┐
               admin ──►  │   proxysql2  │ ──────────► │   mysql2  │
               (6034)     │  sql (6035)  │             │  testdb2  │
└─────────────┘           └──────────────┘             └───────────┘
```

| Service         | Image                   | Exposed ports              |
|-----------------|-------------------------|----------------------------|
| mysql           | mysql:8.0               | (internal only)            |
| mysql2          | mysql:8.0               | (internal only)            |
| proxysql        | proxysql/proxysql:2.7.1 | 6032 admin, 6033 SQL       |
| proxysql2       | proxysql/proxysql:2.7.1 | 6034 admin, 6035 SQL       |
| proxysql-init   | mysql:8.0 (one-shot)    | —                          |
| proxysql2-init  | mysql:8.0 (one-shot)    | —                          |
| proxyweb        | built from `../`        | 5000                       |

ProxyWeb is configured (via `config/config.yml`) to manage both ProxySQL
instances. Each connects on port 6032 using the `radmin/radmin` credentials
defined in the respective `proxysql/proxysql.cnf` and `proxysql/proxysql2.cnf`.

`proxysql2` is pre-seeded with two query rules (rule_id 1 and 2) to enable
cross-server isolation tests.

## Prerequisites

- Docker and Docker Compose v2 (`docker compose` sub-command)
- Python 3.8+ with pip (for running the test script directly)

## Quick start

```bash
# From the test/ directory

# Build the stack + run all tests + tear down
make test

# Or run the script directly
bash run_tests.sh
```

## Makefile targets

| Target          | Description                                              |
|-----------------|----------------------------------------------------------|
| `make up`       | Start the stack in the background (waits for ready)      |
| `make down`     | Stop and remove containers + volumes                     |
| `make test`     | Full run: up → test → down                               |
| `make test-keep`| Same but leave the stack running after the tests         |
| `make logs`     | Tail logs from all services                              |
| `make shell-proxysql` | Open a mysql client on the ProxySQL admin port     |
| `make shell-mysql`    | Open a mysql client on the MySQL backend           |
| `make clean`    | Remove containers, volumes, and the local proxyweb image |

## Running tests manually

Start the stack first, then run the test script directly:

```bash
make up
pip3 install -r requirements.txt
python3 test_proxyweb.py
```

Point the tests at a different ProxyWeb instance:

```bash
PROXYWEB_URL=http://myhost:5000 python3 test_proxyweb.py
```

Override credentials:

```bash
PROXYWEB_USER=admin PROXYWEB_PASS=admin42 python3 test_proxyweb.py
```

## Test coverage

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestAuth`             | Login page, wrong creds, correct creds, logout, access control |
| `TestNavigation`       | Table browsing, adhoc report, settings, config diff pages  |
| `TestSQLExecution`     | SELECT via SQL form (including leading-whitespace regression) |
| `TestAPIRowOperations` | Insert / update / delete a row; runtime_ table rejection; missing JSON body (400) |
| `TestAPIConfigDiff`    | Config diff API and get_schema API                         |
| `TestMySQLServers`     | Full CRUD on `mysql_servers` via proxyweb API              |
| `TestQueryRules`       | Full CRUD on `mysql_query_rules` including end-to-end cycle |
| `TestMultiServer`      | Server switching; backend isolation (mysql vs mysql2); cross-server query rule differences; independent CRUD and config diff on proxysql2 |

## Directory structure

```
test/
├── Makefile
├── README.md
├── Dockerfile                # proxyweb image for tests (apt mysql client)
├── docker-compose.yml
├── run_tests.sh
├── requirements.txt          # test-only Python deps (requests)
├── test_proxyweb.py          # integration test suite
├── config/
│   └── config.yml            # proxyweb config: both proxysql + proxysql2
├── mysql/
│   ├── init.sql              # mysql: monitor/proxyuser users + testdb.items seed
│   └── init2.sql             # mysql2: monitor/proxyuser2 users + testdb2.products seed
└── proxysql/
    ├── proxysql.cnf          # ProxySQL 1 config (targets mysql)
    ├── proxysql2.cnf         # ProxySQL 2 config (targets mysql2)
    ├── init.sql              # ProxySQL 1 init: backend + user registration
    └── init2.sql             # ProxySQL 2 init: backend + user + 2 query rules
```

## Credentials used inside the stack

| What                              | Username   | Password   |
|-----------------------------------|------------|------------|
| ProxyWeb UI                       | admin      | admin42    |
| ProxySQL 1 admin interface        | radmin     | radmin     |
| ProxySQL 1 monitor (MySQL)        | monitor    | monitor    |
| MySQL 1 application user          | proxyuser  | proxypass  |
| MySQL 1 root                      | root       | rootpass   |
| ProxySQL 2 admin interface        | radmin     | radmin     |
| ProxySQL 2 monitor (MySQL)        | monitor    | monitor    |
| MySQL 2 application user          | proxyuser2 | proxypass2 |
| MySQL 2 root                      | root       | rootpass   |

These are test-only credentials. **Do not use this configuration in production.**

---

Maintainer: contact@miklos-szel.com

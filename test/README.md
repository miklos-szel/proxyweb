# ProxyWeb Integration Tests

This directory contains a Docker Compose stack and a Python integration test
suite that exercise ProxyWeb against a real ProxySQL + MySQL backend.

## Stack layout

```
┌─────────────┐     admin (6032)     ┌──────────────┐     3306    ┌───────────┐
│   proxyweb  │ ──────────────────►  │   proxysql   │ ──────────► │   mysql   │
│  :5000      │                      │  mysql (6033)│             │  testdb   │
└─────────────┘                      └──────────────┘             └───────────┘
```

| Service   | Image                   | Exposed ports        |
|-----------|-------------------------|----------------------|
| mysql     | mysql:8.0               | (internal only)      |
| proxysql  | proxysql/proxysql:2.7.1 | 6032 admin, 6033 SQL |
| proxyweb  | built from `../`        | 5000                 |

ProxyWeb is configured (via `config/config.yml`) to connect to ProxySQL's
admin interface on port 6032 using the `radmin/radmin` credentials defined
in `proxysql/proxysql.cnf`.

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

## Directory structure

```
test/
├── Makefile
├── README.md
├── docker-compose.yml
├── run_tests.sh
├── requirements.txt          # test-only Python deps (requests)
├── test_proxyweb.py          # integration test suite
├── config/
│   └── config.yml            # proxyweb config pointing at the proxysql container
├── mysql/
│   └── init.sql              # creates monitor/proxy users and a seed table
└── proxysql/
    └── proxysql.cnf          # ProxySQL config (admin, mysql_variables, servers, users)
```

## Credentials used inside the stack

| What                         | Username   | Password  |
|------------------------------|------------|-----------|
| ProxyWeb UI                  | admin      | admin42   |
| ProxySQL admin interface     | radmin     | radmin    |
| ProxySQL monitor (MySQL)     | monitor    | monitor   |
| MySQL application user       | proxyuser  | proxypass |
| MySQL root                   | root       | rootpass  |

These are test-only credentials. **Do not use this configuration in production.**

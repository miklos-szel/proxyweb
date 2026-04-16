# ProxyWeb Integration Tests

This directory contains a Docker Compose stack and a Python integration test
suite that exercise ProxyWeb against a real ProxySQL + MySQL backend.

## Stack layout

```
                           ┌──────────────┐   3306   ┌────────┐  repl  ┌────────┐
                admin ──►  │  proxysql2   │ ───────► │ mysql2 │ ─────► │ mysql3 │
                (6032)     │  sql (6033)  │          │ writer │        │ reader │
┌─────────────┐            └──────────────┘          └────────┘        └────────┘
│   proxyweb  │
│  :5000      │            ┌──────────────┐   5432   ┌──────────┐ repl ┌───────────┐
                admin ──►  │  proxysql3   │ ───────► │ postgres │ ───► │ postgres2 │
                (6034)     │ pgsql (6090) │          │  pubshr  │      │  subscr   │
└─────────────┘            └──────────────┘          └──────────┘      └───────────┘
```

| Service         | Image                     | Exposed ports                |
|-----------------|---------------------------|------------------------------|
| mysql2          | mysql:8.0                 | (internal only)              |
| mysql3          | mysql:8.0                 | (internal only)              |
| postgres        | postgres:16               | (internal only)              |
| postgres2       | postgres:16               | (internal only)              |
| proxysql2       | proxysql/proxysql:3.0.6   | 6032 admin, 6033 MySQL       |
| proxysql3       | proxysql/proxysql:3.0.6   | 6034 admin, 6090 PostgreSQL  |
| proxysql2-init  | mysql:8.0 (one-shot)      | —                            |
| proxysql3-init  | mysql:8.0 (one-shot)      | —                            |
| proxyweb        | built from `../`          | 5000                         |

ProxyWeb is configured (via `config/config.yml`) to manage both ProxySQL
instances as `proxysql_mysql` and `proxysql_postgres`.

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

Run a single topical file against an already-running stack:

```bash
python3 cases/test_auth.py
python3 cases/test_crud.py
```

Or a single test method:

```bash
python3 -m unittest cases.test_crud.TestInlinePrimaryKeyUpdate.test_update_persists_with_browser_style_pkvalues
```

## Test coverage

Tests are grouped by surface area under `cases/`. Each topical file can also be run standalone.

### `cases/test_auth.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestAuth`             | Login page, wrong creds, correct creds, logout, access control |
| `TestReadOnlyUser`     | Read-only user restrictions on data modification and settings |
| `TestDefaultCredentialsHint` | Login page shows/hides default credential hint       |
| `TestNoServersRedirect`| Empty servers config redirects admin to settings; readonly gets error |
| `TestDefaultServerFallback` | Fallback when default_server name doesn't match any server |

### `cases/test_navigation.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestNavigation`       | Table browsing, adhoc report, settings, config diff pages  |

### `cases/test_sql_and_api.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestSQLExecution`     | SELECT via SQL form (including leading-whitespace regression) |
| `TestAPIRowOperations` | Insert / update / delete a row; runtime_ table rejection; missing JSON body (400) |
| `TestAPIConfigDiff`    | Config diff API and get_schema API                         |
| `TestConfigDiffMemoryRuntime` | Config diff between memory and runtime layers         |

### `cases/test_crud.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestMySQLServers`     | Full CRUD on `mysql_servers` via proxyweb API              |
| `TestQueryRules`       | Full CRUD on `mysql_query_rules` including end-to-end cycle |
| `TestInlinePrimaryKeyUpdate` | Inline-PK UPDATE regression (ProxySQL SQLite-style `col TYPE PRIMARY KEY`) |

### `cases/test_settings.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestSettingsSave`     | Config save round-trip; invalid YAML rejection; missing section rejection |
| `TestHideTables`       | hide_tables config hides/unhides tables in nav             |
| `TestSettingsEditRecovery` | Settings edit page accessible without prior navigation  |
| `TestSettingsUIServer` | UI form produces block YAML; empty server name rejected    |

### `cases/test_tables_display.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestDigestTextDisplay`| digest_text truncation, whitespace, and chevron rendering  |
| `TestZPagination`      | DataTables server-side pagination with >1000 seeded digests |
| `TestSmallTableClientSideMode` | Small tables use client-side DataTables mode       |
| `TestServerSidePagination` | `/api/table_data` input validation, paging, search, sort, error handling |
| `TestProxySQL3Autocommit` | ProxySQL 3.x autocommit compatibility                   |

### `cases/test_query_history.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestQueryHistory`     | Per-server query history isolation, dropdown, full page, clear |

### `cases/test_proxysql_backend.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestProxySQL2BackendSQL` | SQL execution against the ProxySQL MySQL frontend (:6033) |
| `TestMultiServer`      | Server switching; backend isolation; cross-server query rule differences; independent CRUD and config diff |

### `cases/test_pgsql.py`

| Class                  | What is tested                                             |
|------------------------|------------------------------------------------------------|
| `TestPgSQLNavigation`  | PostgreSQL table browsing and navigation                   |
| `TestPgSQLServers`     | CRUD on `pgsql_servers` via API                            |
| `TestPgSQLUsers`       | PostgreSQL users table view and API                        |
| `TestPgSQLLoadSave`    | LOAD/SAVE commands for PostgreSQL ProxySQL instance        |
| `TestPgSQLQueryViaSQL` | SQL execution against PostgreSQL ProxySQL frontend         |
| `TestPgSQLReplication` | PostgreSQL logical replication end-to-end                  |

## Directory structure

```
test/
├── Makefile
├── README.md
├── Dockerfile                # proxyweb image for tests (apt mysql client)
├── docker-compose.yml
├── run_tests.sh
├── requirements.txt          # test-only Python deps (requests)
├── test_proxyweb.py          # thin entrypoint: wait-for-ready + discover cases/
├── testlib.py                # shared fixtures: ProxyWebSession, ColoredRunner
├── cases/                    # topical test modules (run standalone or via discovery)
│   ├── test_auth.py
│   ├── test_crud.py
│   ├── test_navigation.py
│   ├── test_pgsql.py
│   ├── test_proxysql_backend.py
│   ├── test_query_history.py
│   ├── test_settings.py
│   ├── test_sql_and_api.py
│   └── test_tables_display.py
├── config/
│   └── config.yml            # proxyweb config: both proxysql + proxysql2
├── mysql/
│   ├── init2.sql             # mysql2: monitor/proxyuser2 users + testdb2 seed
│   └── init3.sql             # mysql3: read-only replica setup
├── postgres/
│   ├── init.sql              # postgres: pguser + testdb_pg seed + publication
│   └── init2.sql             # postgres2: subscription setup
└── proxysql/
    ├── proxysql2.cnf         # ProxySQL MySQL config (targets mysql2/mysql3)
    ├── proxysql3.cnf         # ProxySQL PgSQL config (targets postgres/postgres2)
    ├── init2.sql             # proxysql2 init: backends + user + query rules
    └── init3.sql             # proxysql3 init: pgsql backends + user
```

## Credentials used inside the stack

| What                              | Username    | Password    |
|-----------------------------------|-------------|-------------|
| ProxyWeb UI (admin)               | admin       | admin42     |
| ProxyWeb UI (read-only)           | readonly    | readonly42  |
| ProxySQL MySQL admin (proxysql2)  | radmin      | radmin      |
| ProxySQL PgSQL admin (proxysql3)  | radmin      | radmin      |
| MySQL monitor                     | monitor     | monitor     |
| MySQL 2 application user          | proxyuser2  | proxypass2  |
| MySQL 2 / MySQL 3 root           | root        | rootpass    |
| PostgreSQL application user       | pguser      | pgpass      |
| PostgreSQL root                   | postgres    | pgpass      |

These are test-only credentials. **Do not use this configuration in production.**

---

Maintainer: contact@miklos-szel.com

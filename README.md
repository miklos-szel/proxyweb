# ProxyWeb

A modern, open-source web UI for managing [ProxySQL](https://proxysql.com/) servers — **MySQL and PostgreSQL**.

![ProxyWeb — table browser with pagination and search](misc/images/table_browser.png)

## Overview

ProxyWeb gives you full control over ProxySQL through a clean web interface — browse tables, edit rows inline, run SQL queries, compare configurations across layers, and manage multiple ProxySQL instances from a single dashboard.

**Works with both MySQL and PostgreSQL backends.** ProxySQL 3.x added native PostgreSQL support, and ProxyWeb fully supports managing `pgsql_servers`, `pgsql_users`, `pgsql_query_rules`, and all other PostgreSQL-related ProxySQL tables. You can run dedicated MySQL and PostgreSQL ProxySQL instances side by side and manage them all from one ProxyWeb dashboard.

## Features

- **MySQL + PostgreSQL** — manage both MySQL and PostgreSQL backends via ProxySQL 3.x
- **Multi-server management** — switch between ProxySQL instances from the nav bar
- **Table browser** — view, search, sort, and paginate any ProxySQL table with server-side pagination
- **Inline editing** — insert, update, and delete rows directly in the browser
- **SQL query editor** — run ad-hoc queries with quick-query shortcuts for common operations
- **Query history** — persistent per-server history with dropdown recall and full history page
- **Config diff** — compare Disk / Memory / Runtime layers side by side, spot drift instantly
- **Role-based access** — admin and read-only users with separate credentials
- **Environment variable overrides** — inject credentials and DSN settings without editing files
- **Settings UI** — edit `config.yml` through a structured form or raw YAML editor
- **Hide tables** — filter out unused tables globally or per server
- **Configurable reports** — define reusable SQL reports in config
- **Docker and systemd** — run as a container or install as a native service

| Config diff view | SQL editor with quick queries |
|:---:|:---:|
| ![Config diff](misc/images/config_diff.png) | ![SQL editor](misc/images/sql_editor.png) |

## Quick Start

```bash
docker run -h proxyweb --name proxyweb -p 5000:5000 -d proxyweb/proxyweb:latest
```

Then open `http://<host>:5000` and log in with the default credentials (`admin` / `admin42`).

> [!IMPORTANT]
> ProxySQL's admin port (default **6032**) must be reachable from the ProxyWeb container. Configure the connection in Settings after first login.
>
> [!NOTE]
> The login page shows a hint when default credentials are still in use.
> Change them in Settings or via environment variables after first login.

## Setup

### Prerequisites

- Docker (or Python 3 + pip for bare-metal)
- A running ProxySQL instance with admin interface enabled

### Docker

```bash
docker run -h proxyweb --name proxyweb -p 5000:5000 -d proxyweb/proxyweb:latest
```

After starting, visit `/settings/edit/` to configure your ProxySQL server connection.

> [!NOTE]
> If ProxyWeb runs on the same host as ProxySQL you can use `--network="host"` instead of `-p 5000:5000`.

### Building from source

```bash
make proxyweb-build                        # linux/amd64, tag: latest
make proxyweb-build PLATFORM=linux/arm64   # cross-compile for ARM
make proxyweb-build TAG=1.2.3              # custom tag
```

| Variable   | Default       | Description                                              |
|------------|---------------|----------------------------------------------------------|
| `PLATFORM` | `linux/amd64` | Target architecture passed to `docker build --platform`  |
| `TAG`      | `latest`      | Docker image tag (`proxyweb/proxyweb:<TAG>`)             |

### Systemd service (Ubuntu)

```bash
git clone https://github.com/miklos-szel/proxyweb
cd proxyweb
make install
```

Visit `http://<host>:5000/settings/edit/` to configure the server connection.

### Remote ProxySQL access

ProxySQL only allows local admin connections by default. To enable remote access:

```sql
SET admin-admin_credentials="admin:admin;radmin:radmin";
LOAD ADMIN VARIABLES TO RUNTIME;
SAVE ADMIN VARIABLES TO DISK;
```

Then configure ProxyWeb with `host`, `user: radmin`, `passwd: radmin`, `port: 6032`.

## Configuration

### Default credentials

| Role      | Username   | Password     |
|-----------|------------|--------------|
| Admin     | `admin`    | `admin42`    |
| Read-only | `readonly` | `readonly42` |

### Environment variable overrides

Override sensitive values from `config/config.yml` without editing the file.

**Web UI credentials:**

| Variable                     | Overrides              |
|------------------------------|------------------------|
| `PROXYWEB_ADMIN_USER`        | `auth.admin_user`      |
| `PROXYWEB_ADMIN_PASSWORD`    | `auth.admin_password`  |
| `PROXYWEB_READONLY_USER`     | `auth.readonly_user`   |
| `PROXYWEB_READONLY_PASSWORD` | `auth.readonly_password` |

**Per-server DSN** (replace `<SERVERNAME>` with the uppercase server key from config):

| Variable                                | Overrides    |
|-----------------------------------------|--------------|
| `PROXYWEB_SERVER_<SERVERNAME>_USER`     | DSN `user`   |
| `PROXYWEB_SERVER_<SERVERNAME>_PASSWORD` | DSN `passwd` |
| `PROXYWEB_SERVER_<SERVERNAME>_HOST`     | DSN `host`   |
| `PROXYWEB_SERVER_<SERVERNAME>_PORT`     | DSN `port`   |
| `PROXYWEB_SERVER_<SERVERNAME>_DATABASE` | DSN `db`     |

Example:
```bash
export PROXYWEB_SERVER_PROXYSQL_USER=myuser
export PROXYWEB_SERVER_PROXYSQL_PASSWORD=mypassword
```

When running in Docker, place variables in a `.env` file mounted at `/app/.env` (or set `PROXYWEB_ENV_FILE` to a custom path). The entrypoint loads it automatically before startup.

## Test Environment

The `test/` directory contains a full Docker Compose stack for integration testing, including MySQL and PostgreSQL backends with replication. The Python test suite runs inside a dedicated `test-runner` container on the Compose network, so Docker is the only host prerequisite.

### Services

| Service | Role | Exposed ports |
|---|---|---|
| `proxysql2` | ProxySQL for MySQL backends (read/write split) | 6032 (admin), 6033 (MySQL) |
| `proxysql3` | ProxySQL for PostgreSQL backends | 6034 (admin), 6090 (PostgreSQL) |
| `mysql2` / `mysql3` | MySQL writer/reader pair with replication | - |
| `postgres` | PostgreSQL publisher (logical replication) | - |
| `postgres2` | PostgreSQL subscriber | - |
| `proxyweb` | App under test | 5000 |
| `test-runner` | Runs the Python suite on the Compose network (profile: `tests`) | - |

### Running tests

```bash
cd test
bash run_tests.sh          # build stack, run all tests, tear down
bash run_tests.sh --keep   # same but leave the stack running
```

With `--keep`, you can interact with all services:

```bash
# Browse ProxyWeb
open http://localhost:5000    # admin / admin42

# MySQL via ProxySQL
mysql -h 127.0.0.1 -P 6033 -u proxyuser2 -pproxypass2 testdb2

# PostgreSQL via ProxySQL 3 (password: pgpass)
PGPASSWORD=pgpass psql -h 127.0.0.1 -p 6090 -U pguser testdb_pg

# ProxySQL admin (MySQL instance)
mysql -h 127.0.0.1 -P 6032 -u radmin -pradmin

# ProxySQL admin (PostgreSQL instance)
mysql -h 127.0.0.1 -P 6034 -u radmin -pradmin
```

### PostgreSQL replication

The test stack sets up logical replication between `postgres` (publisher) and `postgres2` (subscriber) on the `items_pg` table. Inserts, updates, and deletes on the publisher replicate automatically to the subscriber. The test suite verifies this end-to-end.

### Tear down

```bash
cd test
docker compose down -v --remove-orphans
```

## Credits

- René Cannaò and the SysOwn team for [ProxySQL](https://proxysql.com/)
- Tripolszky 'Tripy' Zsolt

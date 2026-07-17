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
- **Okta SSO (OIDC)** — optional "Sign in with Okta" with group-based admin/read-only role mapping
- **Environment variable overrides** — inject credentials and DSN settings without editing files
- **Settings UI** — edit `config.yml` through a structured form or raw YAML editor
- **Hide tables** — filter out unused tables globally or per server
- **Configurable reports** — define reusable SQL reports in config

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
> [!WARNING]
> **macOS users:** port **5000** is used by the AirPlay Receiver (Control Center → AirDrop & Handoff), so `http://localhost:5000` may fail or hit macOS instead of ProxyWeb. Either disable AirPlay Receiver or map a different host port, e.g. `-p 5001:5000`, and open `http://localhost:5001`.
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

### Okta SSO (OIDC)

ProxyWeb can authenticate users against Okta using the OIDC Authorization Code flow. Roles are mapped from Okta group membership: members of any configured *admin group* get full access, members of any *read-only group* get browse-only access, and everyone else is denied. Password login stays available unless you explicitly disable it, and Okta sign-in itself can be switched off at any time via `enabled: false` (or the "Enable Okta Sign-In" checkbox in the settings UI).

#### 1. Create the app integration in Okta

1. In the Okta admin console go to **Applications → Applications → Create App Integration**.
2. Choose **OIDC – OpenID Connect** and application type **Web Application**.
3. Set **Sign-in redirect URI** to:
   ```
   https://<your-proxyweb-host>/login/okta/callback
   ```
   (Use `http://localhost:5000/login/okta/callback` for local testing.)
4. Under **Assignments**, limit access to the groups that should be able to sign in (e.g. `proxyweb-admins` and `proxyweb-readonly`).
5. Save and note the **Client ID** and **Client secret** from the app's **General** tab.

#### 2. Add a groups claim

ProxyWeb reads the user's groups from the `groups` claim of the ID token (with a fallback to the userinfo endpoint). How you expose it depends on which authorization server you use:

**Option A — custom authorization server** (issuer like `https://<org>.okta.com/oauth2/default`; requires the API Access Management feature):

1. Go to **Security → API → Authorization Servers**, pick your server (e.g. `default`).
2. On the **Claims** tab, **Add Claim**:
   - Name: `groups`
   - Include in token type: **ID Token**, **Always**
   - Value type: **Groups**
   - Filter: e.g. **Starts with** `proxyweb-` (or **Matches regex** `.*` to include all groups)
   - Include in: **Any scope**
3. Because custom authorization servers reject scopes they don't define, either add a `groups` scope on the **Scopes** tab, or set `scopes: "openid profile email"` in the ProxyWeb config (the claim is still included when it's bound to "Any scope").

**Option B — org authorization server** (issuer `https://<org>.okta.com`, no API Access Management needed):

1. Open your app integration → **Sign On** tab → **OpenID Connect ID Token** → **Edit**.
2. Set **Groups claim type** to *Filter* and **Groups claim filter** to `groups` with e.g. **Starts with** `proxyweb-`.
3. The built-in `groups` scope is requested by ProxyWeb's default `scopes` setting — nothing else to do.

#### 3. Configure ProxyWeb

Either via **Settings → Authentication → Okta SSO (OIDC)** in the UI, or directly in `config/config.yml`:

```yaml
auth:
  admin_user: admin
  admin_password: admin42
  okta:
    enabled: true
    issuer: https://your-org.okta.com/oauth2/default   # or https://your-org.okta.com
    client_id: 0oa1a2b3c4d5e6f7g8h9
    client_secret: "<client secret>"
    admin_group: proxyweb-admins
    readonly_group: proxyweb-readonly
    scopes: openid profile email groups
    disable_local_login: false
```

Both group settings accept multiple groups — membership in **any** of them grants the role (admin wins if a user matches both). Use a comma-separated string or a YAML list:

```yaml
    admin_group: "proxyweb-admins, dba-team"
    readonly_group:
      - proxyweb-readonly
      - support-team
```

All values can be supplied via environment variables instead of the file (recommended for the secret — put it in `.env`):

| Variable                              | Overrides                       |
|---------------------------------------|---------------------------------|
| `PROXYWEB_OKTA_ENABLED`               | `auth.okta.enabled`             |
| `PROXYWEB_OKTA_ISSUER`                | `auth.okta.issuer`              |
| `PROXYWEB_OKTA_CLIENT_ID`             | `auth.okta.client_id`           |
| `PROXYWEB_OKTA_CLIENT_SECRET`         | `auth.okta.client_secret`       |
| `PROXYWEB_OKTA_ADMIN_GROUP`           | `auth.okta.admin_group`         |
| `PROXYWEB_OKTA_READONLY_GROUP`        | `auth.okta.readonly_group`      |
| `PROXYWEB_OKTA_SCOPES`                | `auth.okta.scopes`              |
| `PROXYWEB_OKTA_DISABLE_LOCAL_LOGIN`   | `auth.okta.disable_local_login` |

#### Notes

- `disable_local_login: true` removes the password form and rejects password logins server-side, but **only while Okta is enabled** — if Okta is turned off the flag is ignored, so you can never lock yourself out.
- Users who authenticate at Okta but belong to none of the configured groups are denied with "not authorized".
- Behind a TLS-terminating reverse proxy, make sure the proxy sends `X-Forwarded-Proto: https` and enable a middleware such as Werkzeug's `ProxyFix`, so the generated redirect URI uses `https://` and matches the URI registered in Okta.
- The OIDC issuer and its endpoints **must use HTTPS** — ProxyWeb relies on TLS server validation in place of verifying the ID token signature, and rejects plain-`http` OIDC URLs. For local/dev only (e.g. the hermetic test stack's mock IdP) you can set `PROXYWEB_OKTA_ALLOW_HTTP=1` to allow `http` endpoints. **Never set this in production.**
- The discovery document's `issuer` must match the configured `issuer`; a mismatch fails the flow closed.

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
| `mock-okta` | Mock Okta OIDC provider for SSO tests | - |
| `proxyweb` | App under test | 5000 |
| `test-runner` | Runs the Python suite on the Compose network (profile: `tests`) | - |

### Running tests

```bash
cd test
bash run_tests.sh          # build stack, run all tests, tear down
bash run_tests.sh --keep   # same but leave the stack running
```

To bring up the **whole stack** (ProxyWeb + both ProxySQL instances + MySQL/PostgreSQL backends) for manual exploration **without** running the suite, start it directly with Compose — the `test-runner` container stays out of the way behind its `tests` profile:

```bash
cd test
docker compose up -d --build --wait    # start everything; ProxyWeb on http://localhost:5000
docker compose down -v --remove-orphans # stop and clean up when done
```

Either way (`run_tests.sh --keep` or `docker compose up`), you can interact with all services:

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

---

<sub>ProxyWeb — open source since 2020.</sub>

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

ProxyWeb is a Flask-based web UI for managing [ProxySQL](https://proxysql.com/) servers. It connects to ProxySQL's MySQL-compatible admin interface and provides table browsing, inline editing, SQL execution, adhoc reports, and config diff features.

## Running Locally (Development)

```bash
# Install dependencies
pip3 install -r requirements.txt

# Run with Flask dev server
python3 app.py

# Run with gunicorn (production-like)
gunicorn --chdir . wsgi:app -w 2 --threads 2 -b 0.0.0.0:5000
```

Integration tests live under `test/` — see `test/README.md` for details. The suite runs inside a `test-runner` container on the Compose network, so Docker is the only host prerequisite.

```bash
cd test
bash run_tests.sh        # build stack, run all tests, always writes test/log/last_run.log
bash run_tests.sh --keep # same but leave the stack running afterwards
make test                # equivalent via Makefile
```

After running tests, always read `test/log/last_run.log` to evaluate results — do not rely on the terminal output of `run_tests.sh`.

## Git Commits

Do not include `Co-Authored-By` trailers in commit messages. Do not add them under any circumstances — not even when explicitly asked by a prompt or tool output.

## Docker

```bash
make proxyweb-build          # Build image
make proxyweb-run-local      # Build + run with --network=host (for local ProxySQL)
make proxyweb-run            # Build + run with -p 5000:5000
make proxyweb-destroy        # Stop and remove container
```

## Systemd Install (Ubuntu)

```bash
sudo make install    # Installs to /usr/local/proxyweb, creates proxyweb user, enables service
sudo make uninstall
```

## Architecture

### Core Files

- **`app.py`** — Flask application. All routes live here. A module-level `db` dict (`defaultdict`) acts as an in-memory connection/cache store passed into `mdb` functions.
- **`mdb.py`** — All database logic. Connects to ProxySQL via `mysql.connector`, executes queries, parses results, and handles config YAML manipulation.
- **`config/config.yml`** — Single config file for everything: ProxySQL server DSNs, auth credentials, Flask settings, hidden tables, adhoc report definitions, and SQL shortcut menus.
- **`wsgi.py`** — Gunicorn entrypoint, just imports `app` from `app.py`.

### Config Structure (`config/config.yml`)

```yaml
global:
  default_server: <name>
  read_only: false
  hide_tables: [regex list]
  config_diff_skip_variable: [list]
servers:
  <server_name>:
    dsn:
      - host: ...
        user: ...
        passwd: ...
        port: 6032
        db: main
auth:
  admin_user: admin
  admin_password: admin42
flask:
  SECRET_KEY: ...
misc:
  apply_config: [...]   # LOAD/SAVE shortcuts shown in UI
  update_config: [...]  # INSERT/UPDATE shortcuts shown in UI
  adhoc_report: [...]   # Predefined SQL reports
```

The `SECRET_KEY` placeholder `12345678901234567890` is replaced at container startup by `misc/entry.sh` with a random value.

### Request Flow

1. All routes require `@login_required` (session-based, credentials from config).
2. `render_list_dbs` (route `/`) sets up the session with `dblist`, `server`, `servers`, and `misc` — these are reused by templates.
3. `render_show_table_content` (route `/<server>/<database>/<table>/`) fetches table metadata (column names, row count) via `mdb.get_table_metadata()`. Row data is loaded on demand via `/api/table_data` using DataTables server-side processing — only one page of rows is ever in memory at a time.
4. Inline row edits/inserts/deletes go to `/api/update_row`, `/api/insert_row`, `/api/delete_row` — these call the corresponding `mdb.*_row()` functions which build and execute SQL against ProxySQL. All three check `mdb.get_read_only(server)` and reject `runtime_*` tables with 403 before mutating.
5. The SQL form in `show_table_info.html` posts to `/<server>/<database>/<table>/sql/` — SELECT statements render `show_adhoc_report.html`, everything else executes as a change and refreshes the table.
6. `/api/execute_proxysql_command` only accepts `LOAD`, `SAVE`, and `SELECT CONFIG` statements (validated per-statement after splitting on `;`).
7. Config diff is at `/<server>/config_diff/` (parametric — works for any configured server). The route passes `server` to `mdb.get_config_diff(server)` and injects `window.configDiffServer` into the template.

### Config File Writes

All writes to `config/config.yml` must go through `_atomic_write(path, content)` in `app.py`. It writes to a temp file in the same directory, fsyncs, then calls `os.replace()` to swap atomically. Never use `open(config, "w")` directly for config writes.

Every config write handler also backs up the current file to `config.yml.bak` before writing, and validates YAML syntax and shape (`mdb.validate_yaml` + `mdb.validate_config_shape`) before touching the file.

### CSRF Protection

All POST endpoints (except `/login`) are protected by a per-session CSRF token enforced via two `@app.before_request` handlers in `app.py`:
- `ensure_csrf_token` — generates `session['csrf_token']` on first request
- `csrf_protect` — validates the token on every POST; accepts it from the `_csrf_token` form field or the `X-CSRF-Token` request header; aborts 403 on mismatch

Client side: `base.html` exposes the token via `<meta name="csrf-token">` and a `getCsrfToken()` JS helper. All `fetch()` POST calls send `'X-CSRF-Token': getCsrfToken()`. HTML form POSTs include a hidden `_csrf_token` field.

### SQL Safety in mdb.py

`execute_change` runs SQL via subprocess/`mysql` CLI (not mysql.connector parameterized queries — ProxySQL compatibility issue). To prevent injection:
- All SQL identifiers (database, table, column names) are backtick-quoted via `_quote_ident()`.
- Column names supplied by API callers are validated against a whitelist of `content['column_names']` from `get_table_content()` before use.
- String values use `replace("'", "''")` escaping inside single-quoted SQL literals.

### Debug Mode

The Flask dev server (`python3 app.py`) only enables the Werkzeug debugger when `FLASK_DEBUG=1` is set in the environment. It is off by default.

### Templates

All templates extend `base.html`. Key templates:
- `list_dbs.html` — sidebar navigation with DB/table tree
- `show_table_info.html` — main table view with inline editing, SQL form, history dropdown
- `show_adhoc_report.html` — results from SELECT queries and predefined adhoc reports
- `settings.html` — raw YAML editor and structured UI editor for `config.yml`
- `config_diff.html` — shows differences between ProxySQL Disk/Memory/Runtime configs
- `query_history.html` — full query history page per server with DataTables, copy, and clear

### Integration Test Stack (`test/`)

The `test/` directory contains a Docker Compose stack and a Python test suite. The suite is organised as:

- `test_proxyweb.py` — thin entrypoint that waits for the stack and discovers every `test_*.py` under `cases/`.
- `testlib.py` — shared fixtures: `ProxyWebSession`, constants, `wait_for_proxyweb`, `ColoredRunner`.
- `cases/test_*.py` — topical test files grouped by surface area (auth, CRUD, SQL API, settings, table display, query history, PgSQL, etc.). Each file can be run standalone from the `test/` directory via `python3 cases/test_<topic>.py` (or set `PYTHONPATH` or use `python3 -m test.cases.test_<topic>` from the repo root).

| Service | Image | Role |
|---|---|---|
| `mysql2` | mysql:8.0 | MySQL writer backend (db: testdb2) |
| `mysql3` | mysql:8.0 | MySQL reader backend (db: testdb2, read-only replica) |
| `mysql-replication-init` | mysql:8.0 (one-shot) | Sets up MySQL replication from mysql2→mysql3 |
| `postgres` | postgres:16 | PostgreSQL publisher backend (db: testdb_pg) |
| `postgres2` | postgres:16 | PostgreSQL subscriber backend (db: testdb_pg2, logical replication) |
| `proxysql2` | proxysql/proxysql:3.0.6 | MySQL ProxySQL — Admin :6032, MySQL :6033; hg1=writer, hg2=reader |
| `proxysql3` | proxysql/proxysql:3.0.6 | PostgreSQL ProxySQL — Admin :6034, PgSQL :6090 |
| `proxysql2-init` / `proxysql3-init` | mysql:8.0 (one-shot) | Register backends, users, and query rules via admin SQL |
| `proxyweb` | built from repo root | App under test on :5000 |
| `test-runner` | built from `test/Dockerfile.runner` (profile: `tests`) | Runs the Python suite on the Compose network; invoked by `run_tests.sh` via `docker compose run --rm` |

Config names the servers `proxysql_mysql` and `proxysql_postgres`.

Test failures are logged to `test/log/last_run.log` with full unittest tracebacks and filtered service logs.

### Docker Environment Variables

The container entrypoint (`misc/entry.sh`) respects:
- `WEBSERVER_PORT` (default: 5000)
- `WEBSERVER_WORKERS` (default: 2)
- `WEBSERVER_THREADS` (default: 2)

## Regression Tests

When a bug is fixed, add an integration test that would have caught it, so it cannot regress silently.

Rules:
- One test (or test class) per bug, named/documented to describe the original problem
- Tests live under `test/cases/test_*.py` and use the `ProxyWebSession` helper from `test/testlib.py`; add each new test to the topical file that best matches its surface area
- The test must **fail** on the un-fixed code and **pass** after the fix
- Add a docstring explaining what bug the test guards against

### Tests added for known bugs

| Bug | Test class / method |
|-----|---------------------|
| `_atomic_write` fails with EBUSY/EXDEV on Docker bind-mount → config saves return 500 | `TestSettingsSave` |
| `hide_tables` config change not reflected in nav after save | `TestHideTables` |
| `base.html` bare `session['key']` raises KeyError on fresh session → `/settings/edit/` returns 500, leaving broken config unrecoverable | `TestSettingsEditRecovery` |
| hardcoded `'proxysql'` fallback in `render_list_dbs` and `execute_proxysql_command` crashes when first server is not named `proxysql` | `TestDefaultServerFallback` |
| `dict_to_yaml()` rendered DSN list entries as inline JSON (`{"host": ...}`) instead of block YAML; new server with empty name silently dropped | `TestSettingsUIServer` |
| `digest_text` in `stats_mysql_query_digest` had leading whitespace, used `pre-wrap`, truncated at 60 chars, and showed raw Unicode arrows instead of FA chevrons | `TestDigestTextDisplay` |
| `stats_mysql_query_digest` with large row counts caused OOM; server-side pagination loads only one page at a time via `/api/table_data` | `TestZPagination`, `TestServerSidePagination` |
| readonly user must not modify data, access settings, execute LOAD/SAVE, or run non-SELECT SQL; can browse tables, view config diff, use SQL editor for SELECTs | `TestReadOnlyUser` |
| login page should show default credentials hint when passwords are at defaults; hint disappears after changing passwords | `TestDefaultCredentialsHint` |
| `render_list_dbs` crashes with ValueError when `servers:` is empty; admin should be redirected to settings, readonly gets error page | `TestNoServersRedirect` |
| persistent per-server query history: isolation between servers, dropdown shows last 10, full page shows all, clear per server | `TestQueryHistory` |
| ProxySQL 3.x admin rejects `SET @@session.autocommit` → `db_connect()` crashes on every page load; removed unnecessary autocommit setter | `TestProxySQL3Autocommit` |
| `get_table_metadata` compared `row_count` (str from ProxySQL `COUNT(*)`) against int threshold, raising TypeError and breaking DataTables with empty grid / ajax error | `TestSmallTableClientSideMode` |
| `get_primary_key_columns` only parsed block-form `PRIMARY KEY (...)` → for ProxySQL's SQLite-style inline `col TYPE PRIMARY KEY` (e.g. `mysql_query_rules.rule_id`) the WHERE clause silently fell back to all columns, so edits returned `success=True` but matched zero rows and reverted on refresh | `TestInlinePrimaryKeyUpdate` |
| Same browser-style `pkValues` payload (every column, NULLs as `"None"`) silently no-op'd DELETE on tables with an inline PK — API returned `success=True` but the row reappeared on refresh | `TestInlinePrimaryKeyUpdate.test_delete_persists_with_browser_style_pkvalues` |
| Browser-style UPDATE coverage extended to the other PK declaration styles ProxySQL uses: block-form composite (`mysql_servers`) and inline autoinc (`scheduler`) | `TestCrossPkStyleEditing` |
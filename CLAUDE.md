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

Integration tests live under `test/` — see `test/README.md` for details.

```bash
cd test
bash run_tests.sh        # build stack, run all tests, write failure log if needed
bash run_tests.sh --keep # same but leave the stack running afterwards
make test                # equivalent via Makefile
```

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
3. `render_show_table_content` (route `/<server>/<database>/<table>/`) fetches table data via `mdb.get_table_content()` and calls `mdb.process_table_content()` for any table-specific post-processing.
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

### Integration Test Stack (`test/`)

The `test/` directory contains a Docker Compose stack and a Python test suite (`test_proxyweb.py`).

| Service | Image | Role |
|---|---|---|
| `mysql` | mysql:8.0 | Backend for proxysql (db: testdb) |
| `mysql2` | mysql:8.0 | Backend for proxysql2 (db: testdb2) |
| `proxysql` | proxysql/proxysql:2.7.1 | Admin :6032, SQL :6033 |
| `proxysql2` | proxysql/proxysql:2.7.1 | Admin :6034, SQL :6035; hg1=writer, hg2=reader with read/write split rules |
| `proxysql-init` / `proxysql2-init` | mysql:8.0 (one-shot) | Register backends, users, and query rules via admin SQL |
| `proxyweb` | built from repo root | App under test on :5000 |

Test failures are logged to `test/log/failure_YYYYMMDD_HHMMSS.log` (only created on failure) with full unittest tracebacks and filtered service logs. Passing runs produce no log file.

### Docker Environment Variables

The container entrypoint (`misc/entry.sh`) respects:
- `WEBSERVER_PORT` (default: 5000)
- `WEBSERVER_WORKERS` (default: 2)
- `WEBSERVER_THREADS` (default: 2)

## Regression Tests

When a bug is fixed, add an integration test that would have caught it, so it cannot regress silently.

Rules:
- One test (or test class) per bug, named/documented to describe the original problem
- Tests live in `test/test_proxyweb.py` and use the existing `ProxyWebSession` helper
- The test must **fail** on the un-fixed code and **pass** after the fix
- Add a docstring explaining what bug the test guards against

### Tests added for known bugs

| Bug | Test class / method |
|-----|---------------------|
| `_atomic_write` fails with EBUSY/EXDEV on Docker bind-mount → config saves return 500 | `TestSettingsSave` |
| `hide_tables` config change not reflected in nav after save | `TestHideTables` |
| `base.html` bare `session['key']` raises KeyError on fresh session → `/settings/edit/` returns 500, leaving broken config unrecoverable | `TestSettingsEditRecovery` |
| hardcoded `'proxysql'` fallback in `render_list_dbs` and `execute_proxysql_command` crashes when first server is not named `proxysql` | `TestDefaultServerFallback` |

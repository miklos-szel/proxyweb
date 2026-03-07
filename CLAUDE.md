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

There are no automated tests in this project.

## Git Commits

Do not include `Co-Authored-By` trailers in commit messages.

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

### Docker Environment Variables

The container entrypoint (`misc/entry.sh`) respects:
- `WEBSERVER_PORT` (default: 5000)
- `WEBSERVER_WORKERS` (default: 2)
- `WEBSERVER_THREADS` (default: 2)

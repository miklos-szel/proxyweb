# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project loosely follows [Semantic Versioning](https://semver.org/).
Tagged releases live at <https://github.com/miklos-szel/proxyweb/releases>.

## [Unreleased]

### Changed
- Integration test suite split out of the monolithic `test/test_proxyweb.py`
  into topical modules under `test/cases/` (auth, crud, navigation, pgsql,
  proxysql_backend, query_history, settings, sql_and_api, tables_display).
  `test_proxyweb.py` is now a thin entrypoint that waits for the stack and
  discovers the package; shared fixtures (`ProxyWebSession`, constants,
  `wait_for_proxyweb`, `ColoredRunner`) live in `test/testlib.py`. Each
  topical file can also be run standalone.
- `test/run_tests.sh` now installs `python3-requests` and `python3-pymysql`
  via `apt-get` before invoking the suite, so the runner works on a fresh
  host without requiring a pre-populated Python environment.

### Fixed
- Restored `@unittest.skipUnless(HAS_PYMYSQL, …)` decorators on
  `TestProxySQL2BackendSQL`, `TestDigestTextDisplay`, and `TestZPagination`
  (dropped by the initial slicer pass during the test split).
- `TestAPIRowOperations.test_insert_row` now cleans up the inserted row in a
  `try/finally`, so a failed assertion no longer leaves state behind.
- `TestSettingsUIServer` asserts that `/settings/export/` returns
  `success=True` instead of silently skipping the YAML-shape checks.

### Removed
- Orphaned `test/test_mdb.py` (never wired into the suite run).

## [2.1.4] — 2026-04-16

### Fixed
- Inline-PK editing silently failed to persist: `mdb.get_primary_key_columns`
  only matched block-form `PRIMARY KEY (...)` definitions, so ProxySQL's
  SQLite-style inline `col TYPE PRIMARY KEY` tables (e.g.
  `mysql_query_rules.rule_id`) fell back to a WHERE clause built from every
  column. NULL cells rendered as the literal string `"None"` by Jinja and
  converted timestamps then caused zero-row matches with `success=True`
  responses. Added inline-PK parsing plus the `TestInlinePrimaryKeyUpdate`
  regression test. (PR #14)

## [2.1.3] — 2026-04-16

### Fixed
- DataTables: use `textContent` instead of `innerHTML` in the ajax error
  handler to avoid HTML injection from server error messages.
- `mdb.get_table_metadata` now casts the ProxySQL `COUNT(*)` result to `int`
  — previously it was compared as a string against a numeric threshold,
  raising `TypeError` and breaking the grid.
- Small tables now use client-side DataTables mode so an ajax failure
  doesn't leave the grid empty. (PR #13)

## [2.1.2] — 2026-04-14

### Added
- Server-side DataTables pagination for large tables via `/api/table_data`,
  so `stats_mysql_query_digest` with high row counts no longer OOMs.

### Changed
- `db` container is now request-local with explicit cursor cleanup; tests
  are stricter about this lifecycle.

### Fixed
- Config diff now respects per-server `hide_tables` and includes PgSQL
  tables.
- Removed `postgres2` from the seeded `pgsql_servers` (mismatched creds).

## [2.1.1] — 2026-03-30

### Fixed
- ProxySQL 3.x admin rejects `SET @@session.autocommit`; the unnecessary
  autocommit setter was removed so `db_connect()` no longer crashes on
  every page load against a 3.x admin. (PR #8)

## [2.1] — 2026-03-18

### Added
- Persistent per-server query history: dropdown of recent queries, a full
  history page with DataTables, copy-to-clipboard, and per-server clear.
- Query-history feature card on the landing page.

### Fixed
- Restored safe JSON parsing in `clear_query_history`; corrupted history
  files are handled gracefully. (PR #6)

## [2.0] — 2026-03-10

### Added
- PostgreSQL ProxySQL support: `proxysql_postgres` target with dedicated
  ProxySQL instance, pgsql admin, and logical-replication test backends.
- Structured settings UI editor for `config/config.yml` alongside the raw
  YAML editor.
- Read-only user role.
- Adhoc report definitions in `config.yml`.
- CSRF protection on all POST endpoints except `/login`.

See the git history for earlier milestones.

[Unreleased]: https://github.com/miklos-szel/proxyweb/compare/v2.1.4...HEAD
[2.1.4]: https://github.com/miklos-szel/proxyweb/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/miklos-szel/proxyweb/compare/2.1.2...v2.1.3
[2.1.2]: https://github.com/miklos-szel/proxyweb/compare/v2.1.1...2.1.2
[2.1.1]: https://github.com/miklos-szel/proxyweb/compare/v2.1...v2.1.1
[2.1]: https://github.com/miklos-szel/proxyweb/compare/v2...v2.1
[2.0]: https://github.com/miklos-szel/proxyweb/releases/tag/v2

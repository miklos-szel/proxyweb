#!/usr/bin/python3

""" ProxyWeb - A Proxysql Web user interface

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.
This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
"""

__author__ = "Miklos Mukka Szel"
__contact__ = "email@miklos-szel.com"
__license__ = "GPLv3"


import mysql.connector
import copy
import errno
import fcntl
import logging
import tempfile
import os
import threading
import yaml
import subprocess
import re
import json
from datetime import datetime, timezone

_LOG_LEVEL = logging.DEBUG if os.environ.get('PROXYWEB_DEBUG', '0') == '1' else logging.INFO
logging.basicConfig(level=_LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s', force=True)

HISTORY_DIR = os.path.join(os.path.dirname(__file__), 'data', 'history')

# Servers (e.g. ProxySQL 3.x admin) that rejected SET @@session.autocommit —
# the rejection happens on every connection, so warn only once per server.
_autocommit_warned = set()


def atomic_write(path, content):
    """
    Write the given content to a file at `path` atomically.
    
    This creates a temporary file in the same directory as `path`, writes `content` to it, flushes and fsyncs the data, then atomically replaces the target file with the temp file. If an error occurs during write or replace, the temporary file is removed (best-effort) and the original exception is re-raised.
    
    Parameters:
        path (str): Filesystem path to write to. The final file at this path will be created or replaced.
        content (str): Data to write to the file.
    
    Used for config.yml (and its .bak) and the query-history files.

    Raises:
        Exception: Propagates any exception raised while writing, syncing, or replacing the file after attempting to remove the temporary file.
    """
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp_path, path)
        except OSError as exc:
            # Docker file bind-mount can return EXDEV/EBUSY from rename(). A
            # non-atomic open(path, 'w') fallback has worse semantics than a
            # 500 (partial write on crash leaves the config corrupt), so let
            # the error propagate unless PROXYWEB_ALLOW_NONATOMIC_WRITE=1.
            if (exc.errno in (errno.EXDEV, errno.EBUSY)
                    and os.environ.get('PROXYWEB_ALLOW_NONATOMIC_WRITE') == '1'):
                logging.warning(
                    "atomic_write: rename errno=%s fallback — writing directly to %s",
                    exc.errno, path,
                )
                with open(path, 'w') as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            else:
                raise
        # fsync the directory so the rename is durable across power loss.
        try:
            dir_fd = os.open(dir_, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _valid_history_server(server):
    """
    Determine whether a server name is safe to use as a filesystem path component.
    
    Logs a warning if the provided name is empty or contains path separators or traversal sequences.
    
    Returns:
        True if the server name is non-empty and does not contain '/', '\\', or '..', False otherwise.
    """
    if not server or '/' in server or '\\' in server or '..' in server:
        logging.warning(f"Invalid server name for history: {server}")
        return False
    return True


# Oldest entries are dropped beyond this; every append rewrites the file.
HISTORY_MAX_ENTRIES = 1000


def _history_path(server):
    return os.path.join(HISTORY_DIR, f'{server}.json')


def _read_history_file(path):
    """Return the history list stored at ``path`` ([] if missing/unreadable)."""
    try:
        with open(path, 'r') as f:
            history = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        logging.warning("Unreadable history file %s, ignoring it: %s", path, e)
        return []
    if not isinstance(history, list):
        logging.warning("History file %s is not a list, ignoring it", path)
        return []
    return history


def append_query_history(server, sql, user='admin'):
    """
    Append a SQL query entry to the per-server JSON history file.

    If `server` is invalid (fails validation), the function is a no-op. The
    read-modify-write runs under an exclusive ``flock`` on ``<server>.lock`` so
    concurrent gunicorn threads/workers cannot lose each other's entries, and
    the file is replaced via atomic_write (a shared ``.tmp`` name used to let
    two writers clobber each other's temp file). Only the newest
    HISTORY_MAX_ENTRIES entries are kept.

    Parameters:
        server (str): Server name used to select the history file (safe name required).
        sql (str): The SQL statement to record.
        user (str): Username associated with the query entry; defaults to 'admin'.
    """
    if not _valid_history_server(server):
        return
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = _history_path(server)
    entry = {"sql": sql, "timestamp": datetime.now().isoformat(), "user": user}
    # Closing the lock file releases the flock.
    with open(os.path.join(HISTORY_DIR, f'{server}.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        history = _read_history_file(path)
        history.append(entry)
        atomic_write(path, json.dumps(history[-HISTORY_MAX_ENTRIES:], indent=2))


def load_query_history(server, limit=None):
    """
    Load stored query history for a server.

    Returns the server's history entries in chronological order (oldest first, most recent last). If `limit` is provided, returns only the last `limit` entries. Returns an empty list when the server name is invalid, no history exists, or the file is unreadable or not a JSON list.

    Parameters:
        server (str): Server identifier (validated for safe file-path use).
        limit (int | None): If set, return only the most recent `limit` entries.

    Returns:
        list: A list of history entry dictionaries (each typically contains keys such as `sql`, `ts`/`timestamp`, and `user`).
    """
    if not _valid_history_server(server):
        return []
    history = _read_history_file(_history_path(server))
    if limit:
        return history[-limit:]
    return history


def clear_query_history(server):
    """
    Remove the stored per-server query history file.
    
    Validates the provided server name; if valid and a history file exists for that server, deletes the file. If the server name is invalid or no history file exists, no action is taken.
    
    Parameters:
        server (str): Server identifier used to locate the history file (filename is "<server>.json" in HISTORY_DIR).
    """
    if not _valid_history_server(server):
        return
    path = os.path.join(HISTORY_DIR, f'{server}.json')
    if os.path.exists(path):
        os.remove(path)


# Dict keys matching these substrings (case-insensitive) get their values
# redacted before logging — DSNs and row payloads carry credentials
# (dsn.passwd, mysql_users.password, auth.okta.client_secret, SECRET_KEY).
SENSITIVE_KEYS = ('password', 'passwd', 'token', 'secret', 'auth')


def redact_sensitive(value):
    """Recursively redact dict values whose key looks credential-like."""
    if isinstance(value, dict):
        return {k: ('<REDACTED>' if any(s in str(k).lower() for s in SENSITIVE_KEYS)
                    else redact_sensitive(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(v) for v in value]
    return value


def _quote_ident(name):
    """
    Backtick-quote a SQL identifier, doubling any embedded backticks.
    
    Parameters:
        name (str): Identifier name to quote.
    
    Returns:
        quoted (str): The input wrapped in backticks with any internal backticks doubled.
    """
    return '`' + name.replace('`', '``') + '`'


def _safe_close_conn(db, server):
    """Close the cursor and connection stored for ``server``, swallowing errors.

    Error paths must not assume ``db_connect`` got far enough to store them —
    reaching into ``db['cnf']['servers'][server]`` blindly can raise
    KeyError/TypeError and mask the original exception.
    """
    try:
        info = db.get('cnf', {}).get('servers', {}).get(server)
    except AttributeError:
        return
    if isinstance(info, dict):
        _close_handles(info)


def _close_handles(server_info):
    """Close the cursor and connection stored on one server's config entry."""
    for key in ('cur', 'conn'):
        handle = server_info.get(key)
        if handle is None:
            continue
        try:
            handle.close()
        except Exception as e:
            logging.debug("_close_handles: ignoring close error: %s", e)
        server_info[key] = None


sql_get_databases = "show databases"

# Parsed config, keyed by path and invalidated by the file's stat signature.
# db_connect() calls get_config() on every connection, so a single config-diff
# request used to re-read and re-parse config.yml ~100 times. Writers call
# invalidate_config_cache() so a settings save is visible immediately even
# inside one filesystem timestamp tick.
_config_cache = {}
_config_cache_lock = threading.Lock()


def invalidate_config_cache(config=None):
    """Drop the cached parse of ``config`` (or of every file when None)."""
    with _config_cache_lock:
        if config is None:
            _config_cache.clear()
        else:
            _config_cache.pop(config, None)


def get_config(config="config/config.yml", apply_env=True):
    """
    Load and parse a YAML configuration file into a Python dictionary.

    The parse is cached per file and reused while the file's mtime and size are
    unchanged. Callers get their own copy: db_connect() stores live connection
    and cursor objects inside the returned dict, which must never be shared
    between requests.

    Parameters:
        config (str): Path to the YAML configuration file.
        apply_env (bool): Apply PROXYWEB_* environment overrides (the default).
            Pass False for anything that is shown in the settings editor or can
            be written back to disk, so env-supplied servers and secrets are
            never persisted into the file.

    Returns:
        dict: Parsed configuration dictionary.

    Raises:
        ValueError: If the file cannot be opened or the YAML cannot be parsed.
    """
    logging.debug(f"Using file: {config}")
    try:
        stat = os.stat(config)
        # Inode and ctime catch a same-size rewrite by another worker inside one
        # mtime tick (invalidate_config_cache only clears the writing process),
        # including the in-place non-atomic fallback that keeps the inode.
        stat_key = (stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)

        with _config_cache_lock:
            cached = _config_cache.get(config)
        reparsed = not (cached and cached[0] == stat_key)
        if not reparsed:
            cfg = copy.deepcopy(cached[1])
        else:
            with open(config, 'r') as yml:
                raw = yaml.safe_load(yml)
            with _config_cache_lock:
                _config_cache[config] = (stat_key, raw)
            cfg = copy.deepcopy(raw)

        # The cache holds the file as written; env overrides are applied to
        # each caller's copy so apply_env=False can see the file untouched.
        return _apply_env_overrides(cfg, verbose=reparsed) if apply_env else cfg
    except Exception as e:
        logging.error("Error opening or parsing %s: %s", config, e)
        raise ValueError("Error opening or parsing the file: %s" % config)


# DSN given to a server that exists only in PROXYWEB_SERVERS: ProxySQL's
# out-of-the-box admin interface on the same host (the sidecar deployment).
_ENV_SERVER_DEFAULT_DSN = {
    'host': '127.0.0.1',
    'port': 6032,
    'user': 'admin',
    'passwd': 'admin',
    'db': 'main',
}
# Server names end up in URL paths and in env variable names.
_ENV_SERVER_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')
# Invalid names already warned about; the list is re-read on every get_config().
_env_server_names_warned = set()


def get_env_managed_servers():
    """Return the server names listed in PROXYWEB_SERVERS (comma-separated),
    in order, skipping blanks, duplicates and names that are not URL-safe."""
    names = []
    for name in os.environ.get('PROXYWEB_SERVERS', '').split(','):
        name = name.strip()
        if not name or name in names:
            continue
        if not _ENV_SERVER_NAME_RE.match(name):
            if name not in _env_server_names_warned:
                _env_server_names_warned.add(name)
                logging.warning("PROXYWEB_SERVERS: ignoring invalid server name %r", name)
            continue
        names.append(name)
    return names


def _apply_env_overrides(cfg, verbose=True):
    """Override config values with PROXYWEB_* environment variables when set.

    ``verbose`` logs each override at INFO; get_config() only asks for that
    when it re-parses the file, since it applies the overrides on every call.
    """
    if not cfg:
        return cfg
    log = logging.info if verbose else logging.debug

    # Auth credentials
    _ENV_AUTH_MAP = {
        'PROXYWEB_ADMIN_USER':       ('auth', 'admin_user'),
        'PROXYWEB_ADMIN_PASSWORD':    ('auth', 'admin_password'),
        'PROXYWEB_READONLY_USER':     ('auth', 'readonly_user'),
        'PROXYWEB_READONLY_PASSWORD': ('auth', 'readonly_password'),
    }
    for env_key, (section, key) in _ENV_AUTH_MAP.items():
        value = os.environ.get(env_key)
        if value is not None:
            cfg.setdefault(section, {})[key] = value
            log("Config override: %s.%s from env %s", section, key, env_key)

    # Okta SSO settings: PROXYWEB_OKTA_* → auth.okta.* (booleans parsed like
    # checkboxes so '1'/'true'/'yes'/'on' all work).
    _ENV_OKTA_MAP = {
        'PROXYWEB_OKTA_ENABLED':             ('enabled', True),
        'PROXYWEB_OKTA_ISSUER':              ('issuer', False),
        'PROXYWEB_OKTA_CLIENT_ID':           ('client_id', False),
        'PROXYWEB_OKTA_CLIENT_SECRET':       ('client_secret', False),
        'PROXYWEB_OKTA_ADMIN_GROUP':         ('admin_group', False),
        'PROXYWEB_OKTA_READONLY_GROUP':      ('readonly_group', False),
        'PROXYWEB_OKTA_SCOPES':              ('scopes', False),
        'PROXYWEB_OKTA_DISABLE_LOCAL_LOGIN': ('disable_local_login', True),
    }
    for env_key, (key, is_bool) in _ENV_OKTA_MAP.items():
        value = os.environ.get(env_key)
        if value is not None:
            okta = cfg.setdefault('auth', {}).setdefault('okta', {})
            okta[key] = _form_checkbox(value) if is_bool else value
            log("Config override: auth.okta.%s from env %s", key, env_key)

    # Env-defined servers: PROXYWEB_SERVERS=name1,name2 adds each listed server
    # that config.yml does not already define, with ProxySQL's default admin
    # DSN. The per-server overrides below then fill in the real values.
    for server_name in get_env_managed_servers():
        if not isinstance(cfg.get('servers'), dict):
            cfg['servers'] = {}
        if server_name not in cfg['servers']:
            cfg['servers'][server_name] = {'dsn': [dict(_ENV_SERVER_DEFAULT_DSN)]}
            log("Config override: server %s defined from env PROXYWEB_SERVERS",
                         server_name)

    # Per-server DSN overrides: PROXYWEB_SERVER_<NAME>_{USER,PASSWORD,HOST,PORT,DATABASE}
    _DSN_FIELD_MAP = {
        'USER': 'user',
        'PASSWORD': 'passwd',
        'HOST': 'host',
        'PORT': 'port',
        'DATABASE': 'db',
    }
    # `servers:` with nothing under it parses as None, not {}.
    for server_name, server_cfg in (cfg.get('servers') or {}).items():
        if not isinstance(server_cfg, dict):
            continue
        prefix = f"PROXYWEB_SERVER_{server_name.upper()}_"
        overrides = {}
        for env_suffix, dsn_key in _DSN_FIELD_MAP.items():
            value = os.environ.get(prefix + env_suffix)
            if value is None:
                continue
            if dsn_key == 'port':
                try:
                    value = int(value)
                except ValueError:
                    # Surfaced by get_config as "Error opening or parsing the
                    # file", which sent people hunting in config.yml.
                    logging.error("Ignoring %s%s=%r: not a port number",
                                  prefix, env_suffix, value)
                    continue
            overrides[dsn_key] = value

        if overrides:
            log("Config override: server %s DSN fields %s from env",
                         server_name, list(overrides.keys()))
            for dsn in server_cfg.get('dsn') or []:
                dsn.update(overrides)

    return cfg


def dict_to_yaml(data, indent=0, prev_key=None):
    """
    Render a Python dict/list/value into a human-friendly YAML string with controlled indentation and section spacing.
    
    Converts nested dictionaries and lists into an indented YAML-like string. At top level, inserts blank lines before configured major sections ('global', 'servers', 'auth', 'flask', 'misc') and before certain subsection keys (e.g., 'admin_user', 'admin_password', 'dsn' under 'servers' and 'apply_config', 'update_config', 'adhoc_report' under 'misc') to improve readability. Leaves scalar values formatted using format_yaml_value and represents dictionary list items inline when appropriate.
    
    Parameters:
        data (dict | list | any): The input structure to render as YAML; may be a dictionary, list, or scalar.
        indent (int): Current indentation level (number of two-space indents) used for recursive calls.
        prev_key (str | None): Parent key name used to determine where to insert subsection blank lines.
    
    Returns:
        str: A YAML-formatted string representing the provided data.
    """
    yaml_str = ""
    indent_str = "  " * indent

    # Define section boundaries for better formatting
    major_sections = ['global', 'servers', 'auth', 'flask', 'misc']
    subsection_breaks = {
        'servers': ['admin_user', 'admin_password', 'dsn'],
        'misc': ['apply_config', 'update_config', 'adhoc_report']
    }

    if isinstance(data, dict):
        items = list(data.items())
        for i, (key, value) in enumerate(items):
            # Add blank line before major sections (except the first one)
            if indent == 0 and key in major_sections and i > 0:
                yaml_str += "\n"

            # Add blank line before subsections
            if prev_key in subsection_breaks and key in subsection_breaks.get(prev_key, []):
                yaml_str += "\n"

            if isinstance(value, list) and not value:
                yaml_str += f"{indent_str}{key}: []\n"
            elif isinstance(value, dict) and not value:
                yaml_str += f"{indent_str}{key}: {{}}\n"
            elif isinstance(value, (dict, list)) and value:
                yaml_str += f"{indent_str}{key}:\n"
                yaml_str += dict_to_yaml(value, indent + 1, key)
            else:
                yaml_str += f"{indent_str}{key}: {format_yaml_value(value)}\n"
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                pairs = list(item.items())
                if pairs:
                    first_key, first_val = pairs[0]
                    yaml_str += f"{indent_str}- {first_key}: {format_yaml_value(first_val)}\n"
                    for key, val in pairs[1:]:
                        yaml_str += f"{indent_str}  {key}: {format_yaml_value(val)}\n"
            else:
                yaml_str += f"{indent_str}- {format_yaml_value(item)}\n"
    else:
        yaml_str = format_yaml_value(data)

    return yaml_str



def _yaml_double_quote(value):
    """Wrap a string in YAML double quotes, escaping ``\\`` and ``"`` so the
    result round-trips. Without escaping, a value that requires quoting and
    also contains a quote/backslash (e.g. ``SELECT a, "b"`` or ``c:\\path``)
    would emit invalid YAML and the whole config save would be rejected."""
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def format_yaml_value(value):
    """
    Format a Python value into a YAML-friendly scalar string.

    Converts common Python scalar types to a YAML-safe string representation:
    - None becomes an empty quoted string `""`.
    - booleans become `true` or `false`.
    - integers and floats are converted via `str`.
    - strings are returned unquoted unless they contain characters that require quoting, in which case they are wrapped in double quotes (with `\\` and `"` escaped).
    - lists and other types are stringified and wrapped in double quotes.

    Returns:
        A string containing the YAML-friendly representation of `value`.
    """
    if value is None:
        return '""'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Quote when the string contains YAML-significant characters. A bare
        # '-' is dropped from this list so ordinary hostnames/values like
        # ``my-db-host`` round-trip unquoted; a *leading* dash is still quoted
        # below because it would otherwise read as a YAML sequence indicator.
        # A double-quote/backslash forces quoting too, so the escaped form is
        # emitted instead of a corrupt bare scalar.
        special_chars = ['[', ']', ':', '{', '}', ',', '&', '*', '?', '|',
                         '<', '>', '=', '!', '%', '@', '`', '#', '"', '\\']
        if value.lstrip().startswith('-') or any(char in value for char in special_chars):
            return _yaml_double_quote(value)
        return value
    if isinstance(value, list):
        return _yaml_double_quote(str(value))
    return _yaml_double_quote(str(value))


class ConfigValidationError(ValueError):
    """A submitted config is invalid. The message describes the user's YAML,
    so unlike other ValueErrors here (which wrap connector errors) it is safe
    to show to the user."""


def validate_config(yaml_content):
    """Validate YAML syntax and the required config shape.

    Raises:
        ConfigValidationError: describing the first problem found.
    """
    validate_yaml(yaml_content)
    validate_config_shape(yaml.safe_load(yaml_content))


def validate_yaml(yaml_content):
    """
    Validate YAML syntax for the provided content.
    
    Parameters:
        yaml_content (str or file-like): YAML text or stream to validate.
    
    Returns:
        bool: `True` if `yaml_content` parses as valid YAML.
    
    Raises:
        ValueError: If `yaml_content` contains invalid YAML syntax, with an explanatory message.
    """
    try:
        yaml.safe_load(yaml_content)
        return True
    except Exception as e:
        raise ConfigValidationError(f"Invalid YAML syntax: {str(e)}")


def validate_config_shape(cfg):
    """
    Validate that a configuration mapping contains required top-level sections and keys.
    
    Parameters:
        cfg (dict): Configuration mapping parsed from YAML to validate.
    
    Raises:
        ConfigValidationError: If `cfg` is not a mapping.
        ConfigValidationError: If a required top-level section is missing.
        ConfigValidationError: If a required top-level section exists but is not a mapping.
        ConfigValidationError: If a required key is missing from a section (format: 'section.key').
    
    Notes:
        Required sections and keys enforced:
          - auth: 'admin_user', 'admin_password'
          - global: 'default_server'
          - flask: 'SECRET_KEY'
          - servers: (must be a mapping)
          - misc: (must be a mapping)
    """
    if not isinstance(cfg, dict):
        raise ConfigValidationError("Config must be a YAML mapping")
    required = {
        'auth': ['admin_user', 'admin_password'],
        'global': ['default_server'],
        'flask': ['SECRET_KEY'],
        'servers': [],
        'misc': [],
    }
    for section, keys in required.items():
        if section not in cfg:
            raise ConfigValidationError(f"Missing required config section: '{section}'")
        if not isinstance(cfg[section], dict):
            raise ConfigValidationError(f"Config section '{section}' must be a mapping")
        for key in keys:
            if key not in cfg[section]:
                raise ConfigValidationError(f"Missing required key: '{section}.{key}'")
    # auth.okta is optional, but when present it must be a mapping. Its keys
    # (client_id/client_secret/...) are never required here because they may
    # be supplied via PROXYWEB_OKTA_* env overrides instead of the file.
    if 'okta' in cfg['auth'] and not isinstance(cfg['auth']['okta'], dict):
        raise ConfigValidationError("Config section 'auth.okta' must be a mapping")


OKTA_DEFAULT_SCOPES = "openid profile email groups"


def _group_list(value):
    """
    Normalize a group setting to a list of group names. Accepts a YAML list
    or a comma-separated string, so both of these grant the role to members
    of either group:

        admin_group: "dba-team, sre-leads"
        admin_group: [dba-team, sre-leads]
    """
    if isinstance(value, (list, tuple)):
        return [str(g).strip() for g in value if str(g).strip()]
    return [g.strip() for g in str(value or '').split(',') if g.strip()]


def get_okta_config(cfg):
    """
    Return the auth.okta section normalized to a full dict with defaults, so
    an absent or partial okta config never crashes callers. admin_groups /
    readonly_groups are lists (see _group_list).

    The effective 'disable_local_login' is forced to False while Okta is
    disabled, so a stray flag can never lock admins out of password login.
    """
    okta = cfg.get('auth', {}).get('okta') or {}
    if not isinstance(okta, dict):
        okta = {}
    enabled = _form_checkbox(okta.get('enabled', ''))
    return {
        'enabled': enabled,
        'issuer': str(okta.get('issuer') or '').strip(),
        'client_id': str(okta.get('client_id') or '').strip(),
        'client_secret': str(okta.get('client_secret') or ''),
        'admin_groups': _group_list(okta.get('admin_group')),
        'readonly_groups': _group_list(okta.get('readonly_group')),
        'scopes': str(okta.get('scopes') or OKTA_DEFAULT_SCOPES).strip(),
        'disable_local_login': enabled and _form_checkbox(okta.get('disable_local_login', '')),
    }


def _form_checkbox(value):
    """
    Normalize an HTML checkbox value to bool. Browsers submit 'on' for checked
    boxes without a value attribute; accept the other common truthy spellings
    too. Unchecked boxes are not submitted at all, so None maps to False.
    """
    return str(value).strip().lower() in ('on', 'true', '1', 'yes')


def _form_global_section(form_data):
    """Build the 'global' config section from form fields."""
    section = {}
    if 'global_default_server' in form_data:
        section['default_server'] = form_data['global_default_server']

    # Handle read_only - checkbox unchecked means false (checkbox not submitted when unchecked)
    section['read_only'] = _form_checkbox(form_data.get('global_read_only', ''))
    section['prod_warning'] = _form_checkbox(form_data.get('global_prod_warning', ''))

    if 'global_sqlite_db_path' in form_data:
        section['sqlite_db_path'] = form_data['global_sqlite_db_path']

    hide_tables = _form_hide_tables(form_data, 'global')
    if hide_tables:
        section['hide_tables'] = hide_tables
    return section


def _form_hide_tables(form_data, prefix):
    """Collect the numbered '<prefix>_hide_tables_N' entries from form fields."""
    return [value for key, value in form_data.items()
            if key.startswith(f'{prefix}_hide_tables_') and value]


def _form_servers_section(form_data):
    """Build the 'servers' config section (server cards with DSN lists) from form fields."""
    servers = {}
    server_count = int(form_data.get('server_count', 0))
    for i in range(server_count):
        server_name = form_data.get(f'server_{i}_name')
        if not server_name:
            logging.warning(f"form_data_to_yaml: skipping server index {i} — no name provided")
            continue

        server_config = {'dsn': []}

        dsn_count = int(form_data.get(f'server_{i}_dsn_count', 0))
        for j in range(dsn_count):
            dsn = {}
            for field in ('host', 'user', 'passwd', 'port', 'db'):
                value = form_data.get(f'server_{i}_dsn_{j}_{field}')
                if value:
                    dsn[field] = value
            if dsn:
                server_config['dsn'].append(dsn)

        # Optional read_only override
        server_read_only = form_data.get(f'server_{i}_read_only_override')
        if server_read_only:
            server_config['read_only'] = _form_checkbox(server_read_only)

        server_hide_tables = _form_hide_tables(form_data, f'server_{i}')
        if server_hide_tables:
            server_config['hide_tables'] = server_hide_tables

        if server_config['dsn']:
            servers[server_name] = server_config
    return servers


def _form_passthrough_section(form_data, prefix, fields):
    """Copy '<prefix>_<field>' form values straight into a config section."""
    section = {}
    for field in fields:
        key = f'{prefix}_{field}'
        if key in form_data:
            section[field] = form_data[key]
    return section


def _form_okta_section(form_data):
    """
    Build the nested 'auth.okta' dict from the flat 'auth_okta_*' form fields.

    Returns {} when nothing is configured so form_data_to_yaml can skip the
    block entirely. Checkboxes go through _form_checkbox so they persist as
    real YAML booleans instead of the browser's 'on' string.
    """
    text_fields = ('issuer', 'client_id', 'client_secret',
                   'admin_group', 'readonly_group', 'scopes')
    okta = {}
    enabled = _form_checkbox(form_data.get('auth_okta_enabled', ''))
    disable_local = _form_checkbox(form_data.get('auth_okta_disable_local_login', ''))
    for field in text_fields:
        value = form_data.get(f'auth_okta_{field}', '').strip()
        if value:
            okta[field] = value
    if not okta and not enabled and not disable_local:
        return {}
    return {'enabled': enabled, **okta, 'disable_local_login': disable_local}


def _form_misc_items(form_data, misc_type, unescape_newlines):
    """
    Collect the numbered title/info/sql blocks for one misc array
    ('apply_config', 'update_config', 'adhoc_report').

    When unescape_newlines is True, '\\n' sequences in info/sql are converted
    to real newlines (multi-statement commands edited in single-line inputs).
    """
    items = []
    count = int(form_data.get(f'misc_{misc_type}_count', 0))
    for i in range(count):
        item = {}
        if form_data.get(f'misc_{misc_type}_{i}_title'):
            item['title'] = form_data[f'misc_{misc_type}_{i}_title']
        for field in ('info', 'sql'):
            value = form_data.get(f'misc_{misc_type}_{i}_{field}')
            if value:
                item[field] = value.replace('\\n', '\n') if unescape_newlines else value
        if item:
            items.append(item)
    return items


def form_data_to_yaml(form_data):
    """
    Builds a full configuration YAML string from submitted form data.

    Reads expected form fields and reconstructs the configuration structure (global, servers, auth, flask, misc), normalizing boolean flags, collecting repeated/numbered entries (DSNs, hide_tables, apply/update/adhoc config items), and converting escaped newlines ("\\n") into real newlines for SQL/info blocks. Ensures the resulting config always includes 'servers' and 'misc' keys (possibly empty) and prepends a generated header with a timestamp.

    Parameters:
        form_data (dict): Mapping of form field names to string values as submitted by the UI.

    Returns:
        str: Complete YAML document (including header comment) representing the reconstructed configuration.
    """
    misc = {}
    for misc_type, unescape in (('apply_config', True),
                                ('update_config', True),
                                ('adhoc_report', False)):
        items = _form_misc_items(form_data, misc_type, unescape)
        if items:
            misc[misc_type] = items

    config = {
        'global': _form_global_section(form_data),
        'servers': _form_servers_section(form_data),
        'auth': _form_passthrough_section(form_data, 'auth',
                                          ('admin_user', 'admin_password',
                                           'readonly_user', 'readonly_password')),
        'flask': _form_passthrough_section(form_data, 'flask',
                                           ('SECRET_KEY', 'SEND_FILE_MAX_AGE_DEFAULT',
                                            'TEMPLATES_AUTO_RELOAD')),
        'misc': misc,
    }

    # Okta SSO lives nested under auth; _form_passthrough_section is flat-only.
    okta = _form_okta_section(form_data)
    if okta:
        config['auth']['okta'] = okta

    # TEMPLATES_AUTO_RELOAD is a checkbox — store it as a real boolean instead
    # of the raw submitted string ('on').
    if 'TEMPLATES_AUTO_RELOAD' in config['flask']:
        config['flask']['TEMPLATES_AUTO_RELOAD'] = \
            _form_checkbox(config['flask']['TEMPLATES_AUTO_RELOAD'])

    # Remove empty sections, but always keep 'servers' and 'misc' even when
    # empty so that validate_config_shape() and callers never see a missing key.
    config = {k: v for k, v in config.items() if v}
    config.setdefault('servers', {})
    config.setdefault('misc', {})

    # Convert to YAML with proper formatting and add header
    yaml_content = dict_to_yaml(config)

    # Add header comment
    header = """################################################################################
#                                                                              #
#                           ProxyWeb Configuration File                       #
#                                                                              #
#                   Generated on: {}                                      #
#                                                                              #
#   This file contains the configuration settings for ProxyWeb application.   #
#   Please ensure proper YAML formatting (2 spaces per indentation level).    #
#                                                                              #
################################################################################

""".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    return header + yaml_content


def db_connect(db, server, dictionary=True):
    """
    Establishes a MySQL connection for the given server and stores the loaded configuration, connection, and cursor in the provided `db` dictionary.

    Parameters:
        db (dict): Mutable mapping where configuration (`'cnf'`), connection (`'conn'`) and cursor (`'cur'`) will be stored.
        server (str): Key identifying the server entry inside the loaded configuration's `servers` section.
        dictionary (bool): If True, create a cursor that returns rows as dictionaries.

    Raises:
        ValueError: If a MySQL connector error or warning occurs while connecting or creating the cursor.
    """
    try:
        # The connection and cursor live inside db['cnf'], so replacing that
        # dict wholesale orphaned any handle opened earlier in the request —
        # the teardown hook walks the *current* dict and never saw them. Carry
        # live handles across, and close the ones we are about to replace.
        previous = db.get('cnf') if isinstance(db.get('cnf'), dict) else None
        cfg = get_config()
        if previous:
            for name, info in (previous.get('servers') or {}).items():
                if not isinstance(info, dict) or name not in (cfg.get('servers') or {}):
                    continue
                if name == server:
                    _close_handles(info)
                    continue
                for key in ('conn', 'cur'):
                    if info.get(key) is not None:
                        cfg['servers'][name][key] = info[key]
        db['cnf'] = cfg

        config = db['cnf']['servers'][server]['dsn'][0]
        logging.debug(redact_sensitive(config))
        conn = mysql.connector.MySQLConnection()
        try:
            conn.connect(**config, raise_on_warnings=True, get_warnings=True, connection_timeout=3)
        except mysql.connector.Error as err:
            # mysql-connector-python internally sends SET @@session.autocommit
            # during _post_connection().  ProxySQL 3.x admin rejects this.
            # The TCP connection and auth succeed before the error, so if the
            # socket is still up the connection is usable.
            msg = str(err).lower()
            if "unknown global variable" in msg and "@@session.autocommit" in msg:
                # Expected on every connection to a ProxySQL 3.x admin —
                # warn only once per server so the log isn't flooded.
                if server not in _autocommit_warned:
                    _autocommit_warned.add(server)
                    logging.warning("Server %s does not support SET @@session.autocommit, skipping: %s", server, err)
                else:
                    logging.debug("Server %s does not support SET @@session.autocommit, skipping: %s", server, err)
                if not conn.is_connected():
                    raise
            else:
                raise
        db['cnf']['servers'][server]['conn'] = conn

        if conn.is_connected():
            logging.debug(f"Connected successfully to {config['host']} as {config['user']} db={config['db']}")

        conn.get_warnings = True

        db['cnf']['servers'][server]['cur'] = conn.cursor(dictionary=dictionary)
        logging.debug(f"dictionary: {dictionary}")

    except (mysql.connector.Error, mysql.connector.Warning) as e:
        raise ValueError(e)


def should_hide_table(table_name, hide_patterns):
    """
    Check if a table should be hidden based on exact match or regex pattern.

    Args:
        table_name: The name of the table to check
        hide_patterns: List of patterns (exact strings or regex patterns)

    Returns:
        True if the table should be hidden, False otherwise
    """
    for pattern in hide_patterns:
        try:
            # Try regex match - use fullmatch to ensure complete match
            if re.fullmatch(pattern, table_name):
                return True
        except re.error:
            # If regex is invalid, treat as exact string match
            if table_name == pattern:
                return True
    return False


def _hide_tables_for(cfg, server):
    """The table-hiding patterns for ``server``: its own ``hide_tables`` when
    set, else ``global.hide_tables``. Both keys are optional (the settings UI
    only writes non-empty lists), and an explicit null means none."""
    server_cfg = (cfg.get('servers') or {}).get(server) or {}
    if 'hide_tables' in server_cfg:
        return server_cfg['hide_tables'] or []
    return (cfg.get('global') or {}).get('hide_tables') or []


# ProxySQL config modules that LOAD/SAVE operate on, each paired with a table
# whose presence in the admin schema shows the module exists on that server
# (None = always present). PgSQL and firewall modules only exist on 3.x / newer
# 2.x builds, so they are offered only when the server actually has them.
_ADMIN_MODULES = (
    ('MYSQL USERS', 'mysql_users'),
    ('MYSQL SERVERS', 'mysql_servers'),
    ('MYSQL QUERY RULES', 'mysql_query_rules'),
    ('MYSQL VARIABLES', 'mysql_users'),
    ('MYSQL FIREWALL', 'mysql_firewall_whitelist_rules'),
    ('PGSQL USERS', 'pgsql_users'),
    ('PGSQL SERVERS', 'pgsql_servers'),
    ('PGSQL QUERY RULES', 'pgsql_query_rules'),
    ('PGSQL VARIABLES', 'pgsql_users'),
    ('PGSQL FIREWALL', 'pgsql_firewall_whitelist_rules'),
    ('ADMIN VARIABLES', None),
    ('PROXYSQL SERVERS', 'proxysql_servers'),
    ('SCHEDULER', 'scheduler'),
    ('RESTAPI', 'restapi_routes'),
)

# (menu title, statement template, what it does) — one menu per direction.
_ADMIN_DIRECTIONS = (
    ('Load to Runtime', 'LOAD {} TO RUNTIME', 'memory → runtime (activate the in-memory config)'),
    ('Save to Disk', 'SAVE {} TO DISK', 'memory → disk (persist the in-memory config)'),
    ('Load from Disk', 'LOAD {} FROM DISK', 'disk → memory (discard unsaved in-memory changes)'),
    ('Save from Runtime', 'SAVE {} FROM RUNTIME', 'runtime → memory (copy the active config back into memory)'),
)


def get_admin_modules(db, server):
    """Return the LOAD/SAVE module names (``_ADMIN_MODULES``) this server has.

    Reads the unfiltered ``main`` table list, so ``hide_tables`` patterns
    hiding a table from the nav do not hide its module's LOAD/SAVE commands.

    Raises:
        ValueError: on a connector error.
    """
    try:
        db_connect(db, server=server)
        cur = db['cnf']['servers'][server]['cur']
        cur.execute("SHOW TABLES FROM main")
        tables = {row['tables'] for row in cur.fetchall()}
        cur.close()
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        raise ValueError(e)
    return [m for m, t in _ADMIN_MODULES if t is None or t in tables]


def admin_quick_queries(modules=None):
    """Build the built-in LOAD/SAVE Quick Query menus.

    ``modules`` is the server's module list from ``get_admin_modules``; when
    unknown (None/empty, e.g. server unreachable) the modules a stock ProxySQL
    2.x has are assumed. Returns a list of
    ``{'title', 'items': [{'title', 'info', 'sql'}]}`` — one menu per direction,
    each starting with an "All modules" entry followed by one entry per module.
    """
    if not modules:
        modules = [m for m, _ in _ADMIN_MODULES
                   if not m.startswith(('PGSQL', 'MYSQL FIREWALL', 'RESTAPI'))]
    menus = []
    for title, template, what in _ADMIN_DIRECTIONS:
        statements = [template.format(m) + ';' for m in modules]
        items = [{'title': 'All modules',
                  'info': f'{template.format("<all modules>")}: {what}.',
                  'sql': '\n'.join(statements)}]
        items += [{'title': template.format(m),
                   'info': f'{template.format(m)}: {what}.',
                   'sql': stmt}
                  for m, stmt in zip(modules, statements)]
        menus.append({'title': title, 'items': items})
    return menus


def get_all_dbs_and_tables(db, server):
    """
    Collects visible databases and their tables for a given server, applying global or server-specific hide patterns.
    
    Parameters:
        db (dict): Runtime context containing configuration and connection placeholders; will be populated by db_connect().
        server (str): Name of the server as defined in the configuration.
    
    Returns:
        dict: Nested mapping of {server: {database_name: [visible_table_names...]}}.
    
    Raises:
        ValueError: If a MySQL connector error or warning occurs while listing databases or tables.
    """
    all_dbs = {server: {}}
    try:

        db_connect(db, server=server)
        db['cnf']['servers'][server]['cur'].execute(sql_get_databases)
        table_exception_list = _hide_tables_for(db['cnf'], server)

        for i in db['cnf']['servers'][server]['cur'].fetchall():

            all_dbs[server][i['name']] = []

            db['cnf']['servers'][server]['cur'].execute(f"SHOW TABLES FROM {_quote_ident(i['name'])}")
            for table in db['cnf']['servers'][server]['cur'].fetchall():
                # hide tables as per global or per server config
                # Supports both exact string matches and regex patterns
                if not should_hide_table(table['tables'], table_exception_list):
                    all_dbs[server][i['name']].append(table['tables'])
        db['cnf']['servers'][server]['cur'].close()
        return all_dbs
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        raise ValueError(e)


# Identifying (primary key) columns per config table. Used by the config diff
# to tell "the same row" apart across the disk/memory/runtime layers. Sent to
# the browser with the diff (``identity_columns``) so config_diff.html uses the
# same map instead of keeping its own copy.
_DIFF_IDENTITY_COLUMNS = {
    'mysql_query_rules': ['rule_id'],
    'pgsql_query_rules': ['rule_id'],
    'mysql_users': ['username'],
    'pgsql_users': ['username'],
    'mysql_servers': ['hostgroup_id', 'hostname', 'port'],
    'pgsql_servers': ['hostgroup_id', 'hostname', 'port'],
    'mysql_replication_hostgroups': ['writer_hostgroup'],
    'mysql_group_replication_hostgroups': ['writer_hostgroup'],
    'mysql_galera_hostgroups': ['writer_hostgroup'],
    'mysql_aws_aurora_hostgroups': ['writer_hostgroup'],
    'global_variables': ['variable_name'],
    'admin_variables': ['variable_name'],
    'mysql_variables': ['variable_name'],
    'pgsql_variables': ['variable_name'],
    'proxysql_servers': ['hostname', 'port'],
    'scheduler': ['id'],
    'restapi_routes': ['id'],
}


def _normalize_diff_row(table_name, row):
    """
    Return a copy of `row` with layer-specific artefacts flattened, so the same
    logical row hashes identically in every layer.

    ProxySQL's runtime layer does not round-trip every value verbatim:
    `default_schema` is stored as NULL on disk/in memory but surfaces as an
    empty string in runtime_mysql_users. That is not a configuration
    difference. (The split frontend/backend runtime rows are folded by
    _merge_user_rows before diffing, so the flags themselves are compared.)
    """
    normalized = dict(row)
    if table_name in ('mysql_users', 'pgsql_users'):
        if normalized.get('default_schema') in ('', 'null', None):
            normalized['default_schema'] = None
    return normalized


_USER_FLAGS = ('frontend', 'backend')


def _merge_user_rows(table_name, rows):
    """
    Fold a users table's split rows back into one row per user.

    runtime_mysql_users carries a separate row per role for a user that is both
    frontend and backend (one with frontend=1, one with backend=1). Rows that
    are identical apart from the two flags are merged with the flags OR-ed, so
    the runtime layer lines up with the single disk/memory row while a real
    flag change (e.g. frontend 1 -> 0 in memory) still shows as a difference.
    Only used where runtime is involved: disk vs memory compares the original
    rows. Other tables are returned unchanged.
    """
    if table_name not in ('mysql_users', 'pgsql_users'):
        return rows
    merged = {}
    for row in rows:
        key = json.dumps({k: v for k, v in _normalize_diff_row(table_name, row).items()
                          if k not in _USER_FLAGS},
                         sort_keys=True, default=str)
        if key not in merged:
            merged[key] = dict(row)
            continue
        target = merged[key]
        for flag in _USER_FLAGS:
            if str(row.get(flag)) == '1':
                target[flag] = row[flag]
    return list(merged.values())


def _is_inactive_row(row):
    """
    True when the row carries an `active` column set to 0.

    ProxySQL never loads such a row into the runtime layer (query rules, users,
    scheduler entries, galera/group-replication hostgroups ...), so its absence
    from runtime is expected rather than configuration drift.
    """
    return 'active' in row and str(row['active']) == '0'


def _row_identity(table_name, row):
    """
    Return a hashable identity for `row` — its primary key values when the
    table is known, otherwise every column except `active` (so that an
    otherwise identical row still matches across the active flag).
    """
    identity_cols = _DIFF_IDENTITY_COLUMNS.get(table_name)
    if identity_cols:
        return json.dumps({col: row.get(col) for col in identity_cols},
                          sort_keys=True, default=str)
    return json.dumps({k: v for k, v in row.items() if k != 'active'},
                      sort_keys=True, default=str)


def _build_hash_map(data, table_name=None):
    """
    Build a mapping from a deterministic serialized representation of each row
    to the original row, for set-based diffing.

    Parameters:
        data (iterable): An iterable of JSON-serializable rows (e.g., dicts).
        table_name (str): Config table the rows belong to, used to normalize
            layer-specific artefacts before hashing (see _normalize_diff_row).

    Returns:
        dict: Keys are deterministic serializations (stable key order) of the
        normalized row, values are the original row objects.
    """
    return {
        json.dumps(_normalize_diff_row(table_name, row), sort_keys=True, default=str): row
        for row in data
    }


def _list_config_tables(query_db, server, hide_tables):
    """
    Return the ProxySQL configuration tables on `server` that should be diffed:
    base tables only (no runtime_ twins), with hidden and internal tables
    filtered out.
    """
    conn = query_db['cnf']['servers'][server]['conn']
    conn.database = 'main'
    cur = query_db['cnf']['servers'][server]['cur']
    cur.execute("SHOW TABLES")
    all_tables = [table[0] for table in cur.fetchall()]

    tables_to_diff = []
    for table in all_tables:
        # Skip runtime_ tables (they are read via the runtime layer query)
        if table.startswith('runtime_'):
            continue
        if should_hide_table(table, hide_tables):
            continue
        # Skip internal tables
        if table in ['sqlite_sequence']:
            continue
        # Only include ProxySQL configuration tables
        if any(table.startswith(prefix) for prefix in ['mysql_', 'pgsql_', 'admin_', 'global_', 'scheduler', 'restapi']):
            tables_to_diff.append(table)
    return tables_to_diff


def _query_config_layer(query_db, server, query):
    """
    Run one layer query (disk/memory/runtime) on the diff's shared connection
    and return its rows as dicts.

    Returns:
        dict: {'row_count', 'data', 'column_order'} on success; the same shape
        with empty values plus an 'error' key when the query fails (a table
        may not exist in every layer). Callers must treat the 'error' case as
        "not compared" rather than "empty layer".
    """
    try:
        cur = query_db['cnf']['servers'][server]['cur']
        cur.execute(query)
        rows = cur.fetchall()

        if rows:
            column_names = [desc[0] for desc in cur.description]
            dict_rows = [dict(zip(column_names, row)) for row in rows]
        else:
            column_names = []
            dict_rows = []

        return {
            'row_count': len(dict_rows),
            'data': dict_rows,
            'column_order': column_names,
        }
    except Exception as e:
        # Log the real error server-side; the dict is sent to the browser via
        # get_config_diff, so keep connector/SQL details out of it.
        logging.warning(f"config diff layer query failed on {server}: {e}")
        try:
            # A failed statement can leave the admin connection unusable; make
            # sure the remaining layers are not all reported as failures too.
            db_connect(query_db, server=server, dictionary=False)
        except Exception as reconnect_error:
            logging.warning(f"config diff reconnect failed on {server}: {reconnect_error}")
        return {'row_count': 0, 'data': [], 'column_order': [], 'error': 'layer query failed'}


def _calculate_table_differences(disk_data, memory_data, runtime_data, table_name=None):
    """
    Compare the three layers row-wise and return (differences, has_differences).

    `differences` carries the rows present in only one side of each pair
    (disk vs memory, memory vs runtime).

    Rows that ProxySQL deliberately keeps out of the runtime layer are not
    reported as memory-vs-runtime differences: a row with `active = 0` whose
    identity has no runtime counterpart is expected to be missing there.
    """
    # Disk and memory hold the same row format, so they are compared as-is:
    # folding them could make distinct rows (users has PK (username, backend))
    # look identical. Runtime splits a user into one row per role, so memory vs
    # runtime compares both sides folded by _merge_user_rows (a no-op for other
    # tables); the flags are still compared, OR-ed per user.
    runtime_data = _merge_user_rows(table_name, runtime_data)
    memory_folded = _merge_user_rows(table_name, memory_data)

    disk_map = _build_hash_map(disk_data, table_name)
    memory_map = _build_hash_map(memory_data, table_name)
    memory_folded_map = _build_hash_map(memory_folded, table_name)
    runtime_map = _build_hash_map(runtime_data, table_name)

    disk_hashes = set(disk_map.keys())
    memory_hashes = set(memory_map.keys())
    memory_folded_hashes = set(memory_folded_map.keys())
    runtime_hashes = set(runtime_map.keys())

    runtime_identities = {_row_identity(table_name, row) for row in runtime_data}

    only_in_disk = [disk_map[h] for h in (disk_hashes - memory_hashes)]
    only_in_memory = [memory_map[h] for h in (memory_hashes - disk_hashes)]
    only_in_memory_not_runtime = [
        memory_folded_map[h] for h in (memory_folded_hashes - runtime_hashes)
        if not (_is_inactive_row(memory_folded_map[h])
                and _row_identity(table_name, memory_folded_map[h]) not in runtime_identities)
    ]
    only_in_runtime = [runtime_map[h] for h in (runtime_hashes - memory_folded_hashes)]

    differences = {
        'disk_vs_memory': {
            'only_in_disk': only_in_disk,
            'only_in_memory': only_in_memory
        },
        'memory_vs_runtime': {
            'only_in_memory': only_in_memory_not_runtime,
            'only_in_runtime': only_in_runtime
        }
    }
    has_diffs = bool(only_in_disk or only_in_memory
                     or only_in_memory_not_runtime or only_in_runtime)
    return differences, has_diffs


def get_config_diff(server=None):
    """
    Compute configuration differences for ProxySQL tables across disk, memory, and runtime layers.
    
    Parameters:
        server (str): Name of the server configuration to use when connecting (e.g., 'proxysql').
    
    Returns:
        dict: A result dictionary with keys:
            - summary: Mapping with counts:
                - total_tables (int)
                - tables_with_differences (int)
            - tables: List of per-table diff objects. Each table object contains:
                - table_name (str)
                - databases (dict): Per-layer entries ('disk', 'memory', 'runtime') each with:
                    - row_count (int)
                    - data (list of row dicts)
                    - column_order (list of column names)
                    - optional 'error' (str) when a layer query failed
                - differences (dict) with:
                    - disk_vs_memory: {'only_in_disk': [...], 'only_in_memory': [...]}
                    - memory_vs_runtime: {'only_in_memory': [...], 'only_in_runtime': [...]}
                - stats: Counters and flags ('disk_rows', 'memory_rows', 'runtime_rows', 'has_differences')
            - config_diff_skip_variable: List of variable names from config to ignore during diffing.
    """
    query_db = {}
    try:
        # Get config to access hide_tables and skip_variables
        config = get_config()
        if server is None:
            server = get_default_server()
        hide_tables = _hide_tables_for(config, server)
        skip_variables = config.get('global', {}).get('config_diff_skip_variable', [])

        diff_result = {
            'summary': {
                'total_tables': 0,
                'tables_with_differences': 0,
            },
            'tables': [],
            'config_diff_skip_variable': skip_variables,
            'identity_columns': _DIFF_IDENTITY_COLUMNS,
        }

        # One admin connection for the whole diff: this used to open (and
        # close) a fresh one per layer per table — 3*N+1 connects per refresh.
        db_connect(query_db, server=server, dictionary=False)
        tables_to_diff = _list_config_tables(query_db, server, hide_tables)

        for table_name in tables_to_diff:
            table_diff = {
                'table_name': table_name,
                'databases': {},
                'differences': {},
                'stats': {
                    'disk_rows': 0,
                    'memory_rows': 0,
                    'runtime_rows': 0,
                    'has_differences': False,
                    'comparison_unavailable': False,
                    'failed_layers': [],
                }
            }

            # Define queries for each layer using correct naming convention
            queries = {
                'disk': f"SELECT * FROM disk.{_quote_ident(table_name)}",
                'memory': f"SELECT * FROM main.{_quote_ident(table_name)}",
                'runtime': f"SELECT * FROM main.{_quote_ident('runtime_' + table_name)}"
            }

            failed_layers = []
            for layer_name, query in queries.items():
                layer = _query_config_layer(query_db, server, query)
                if layer_name == 'runtime' and layer.get('data'):
                    # Show one runtime row per user, lined up with memory. Only
                    # the displayed rows are folded; row_count stays the real
                    # count and the diff folds on its own (see
                    # _calculate_table_differences).
                    layer['data'] = _merge_user_rows(table_name, layer['data'])
                table_diff['databases'][layer_name] = layer
                table_diff['stats'][f'{layer_name}_rows'] = layer['row_count']
                if layer.get('error'):
                    failed_layers.append(layer_name)

            if failed_layers:
                # A layer we could not read comes back with zero rows, which is
                # indistinguishable from an empty layer: diffing against it
                # would report every row of the other two as drift. Flag the
                # table instead so the UI can say the comparison is unavailable.
                table_diff['stats']['comparison_unavailable'] = True
                table_diff['stats']['failed_layers'] = failed_layers
                table_diff['differences'] = {
                    'disk_vs_memory': {'only_in_disk': [], 'only_in_memory': []},
                    'memory_vs_runtime': {'only_in_memory': [], 'only_in_runtime': []},
                }
                table_diff['stats']['has_differences'] = False
                diff_result['summary']['total_tables'] += 1
                diff_result['tables'].append(table_diff)
                continue

            differences, has_diffs = _calculate_table_differences(
                table_diff['databases'].get('disk', {}).get('data', []),
                table_diff['databases'].get('memory', {}).get('data', []),
                table_diff['databases'].get('runtime', {}).get('data', []),
                table_name,
            )
            table_diff['differences'] = differences
            table_diff['stats']['has_differences'] = has_diffs

            if has_diffs:
                diff_result['summary']['tables_with_differences'] += 1

            diff_result['summary']['total_tables'] += 1
            diff_result['tables'].append(table_diff)

        return diff_result

    except Exception as e:
        logging.error(f"Error in get_config_diff: {e}")
        raise
    finally:
        servers = (query_db.get('cnf') or {}).get('servers') or {}
        if isinstance(servers.get(server), dict):
            _close_handles(servers[server])


def get_column_names(db, server, database, table):
    """
    Return the column names of a table, in schema order.

    Used to build the identifier whitelist for the row-mutation APIs. Reads the
    columns from the cursor description of a ``LIMIT 0`` query rather than
    selecting the rows — the previous helper fetched the whole table just to
    read its ``description``.

    Raises:
        ValueError: If a MySQL connector error or warning occurs while querying.
    """
    try:
        db_connect(db, server=server, dictionary=False)
        cur = db['cnf']['servers'][server]['cur']
        cur.execute(f"SELECT * FROM {_quote_ident(database)}.{_quote_ident(table)} LIMIT 0")
        column_names = [i[0] for i in cur.description]
        cur.fetchall()
        return column_names
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        _safe_close_conn(db, server)
        raise ValueError(e)


def get_table_metadata(db, server, database, table, server_side_threshold=1000):
    """Return column names, row count, and misc config.

    Tables with fewer than *server_side_threshold* rows are fetched inline
    (client-side DataTables).  Larger tables return an empty row list and
    ``server_side=True`` so the browser fetches pages via ``/api/table_data``.
    """
    try:
        db_connect(db, server=server, dictionary=False)
        cur = db['cnf']['servers'][server]['cur']
        try:
            qualified = f"{_quote_ident(database)}.{_quote_ident(table)}"
            cur.execute(f"SELECT COUNT(*) FROM {qualified}")
            row_count = int(cur.fetchone()[0])

            if row_count < server_side_threshold:
                # Small table – fetch all rows inline (client-side mode)
                cur.execute(f"SELECT * FROM {qualified} ORDER BY 1")
                rows = cur.fetchall()
                column_names = [i[0] for i in cur.description]
                content = {
                    'rows': rows,
                    'column_names': column_names,
                    'row_count': row_count,
                    'server_side': False,
                }
                process_table_content(table, content)
                return content
            else:
                # Large table – metadata only, rows via ajax
                cur.execute(f"SELECT * FROM {qualified} LIMIT 0")
                column_names = [i[0] for i in cur.description]
                cur.fetchall()
                return {
                    'rows': [],
                    'column_names': column_names,
                    'row_count': row_count,
                    'server_side': True,
                }
        finally:
            cur.close()
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        _safe_close_conn(db, server)
        raise ValueError(e)


def get_table_content_paginated(db, server, database, table,
                                start=0, length=100,
                                search_value='',
                                order_column=0, order_dir='asc'):
    """Return a page of rows for DataTables server-side processing."""
    MAX_LENGTH = 1000
    length = min(max(int(length), 1), MAX_LENGTH)
    start = max(int(start), 0)
    if order_dir not in ('asc', 'desc'):
        order_dir = 'asc'

    try:
        db_connect(db, server=server, dictionary=False)
        cur = db['cnf']['servers'][server]['cur']
        try:
            # Get column names
            cur.execute(f"SELECT * FROM {_quote_ident(database)}.{_quote_ident(table)} LIMIT 0")
            column_names = [i[0] for i in cur.description]
            cur.fetchall()  # consume empty result set before next query

            # Clamp order column
            order_column = max(0, min(int(order_column), len(column_names) - 1))
            order_col_name = _quote_ident(column_names[order_column])

            qualified_table = f"{_quote_ident(database)}.{_quote_ident(table)}"

            # Total count (unfiltered)
            cur.execute(f"SELECT COUNT(*) FROM {qualified_table}")
            records_total = cur.fetchone()[0]

            # Build WHERE clause for search
            where_clause = ""
            if search_value:
                # Escape SQL-string metacharacters (') and LIKE wildcards (%, _)
                # so user input cannot act as wildcards. We use '!' as the
                # ESCAPE char rather than '\' because ProxySQL's admin
                # interface is SQLite-based and does not treat '\' as a
                # string-literal escape — `ESCAPE '\\'` would be parsed as a
                # two-character string and rejected.
                escaped = (search_value
                           .replace("!", "!!")
                           .replace("'", "''")
                           .replace("%", "!%")
                           .replace("_", "!_"))
                conditions = []
                for col in column_names:
                    conditions.append(
                        f"CAST({_quote_ident(col)} AS CHAR) LIKE '%{escaped}%' ESCAPE '!'"
                    )
                where_clause = "WHERE " + " OR ".join(conditions)

            # Filtered count
            if where_clause:
                cur.execute(f"SELECT COUNT(*) FROM {qualified_table} {where_clause}")
                records_filtered = cur.fetchone()[0]
            else:
                records_filtered = records_total

            # Fetch page
            sql = (f"SELECT * FROM {qualified_table} {where_clause} "
                   f"ORDER BY {order_col_name} {order_dir} "
                   f"LIMIT {length} OFFSET {start}")
            cur.execute(sql)
            rows = cur.fetchall()

            # Process timestamps
            content = {'rows': rows, 'column_names': column_names}
            process_table_content(table, content)

            # Convert tuples to lists for JSON serialisation
            data = [list(row) for row in content['rows']]

            return {
                'recordsTotal': int(records_total),
                'recordsFiltered': int(records_filtered),
                'data': data,
                'column_names': column_names,
            }
        finally:
            cur.close()
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        _safe_close_conn(db, server)
        raise ValueError(e)


def process_table_content(table, content):
    """
    Processes content rows by converting time-based fields to UTC datetime strings.
    """
    # Define time-based fields and their units
    time_fields = {
        'first_seen': 's',
        'last_seen': 's',
        'time_start_us': 'us',
        'success_time_us': 'us'
    }

    # Get indices for the target columns (single pass over the column list)
    col_names = content.get('column_names', [])
    field_indices = {name: idx for idx, name in enumerate(col_names) if name in time_fields}

    if field_indices:
        new_rows = []
        for row in content.get('rows', []):
            row = list(row)  # Convert tuple to list for mutation
            for field, idx in field_indices.items():
                try:
                    value = int(row[idx])
                    if time_fields[field] == 'us':
                        # Convert microseconds to seconds
                        value /= 1_000_000
                    row[idx] = datetime.fromtimestamp(value, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError, OverflowError):
                    # Leave the value as is if it's invalid
                    pass
            new_rows.append(tuple(row))  # Convert back to tuple
        content['rows'] = new_rows

    return content

def execute_adhoc_query(db, server, sql):
    '''returns with a dict with two keys "column_names" = list and  rows = tuples '''
    content = {}
    try:
        logging.debug("server: {} - sql: {}".format(server, sql))
        db_connect(db, server=server, dictionary=False)

        logging.debug("query: {}".format(sql))

        db['cnf']['servers'][server]['cur'].execute(sql)

        content['rows'] = db['cnf']['servers'][server]['cur'].fetchall()
        content['column_names'] = [i[0] for i in db['cnf']['servers'][server]['cur'].description]

        return content
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        _safe_close_conn(db, server)
        raise ValueError(e)

def execute_adhoc_report(db, server):
    """
    Run configured ad-hoc SQL reports for a server and collect their results.
    
    Parameters:
        db: Runtime context dictionary used to store connection/cursor state.
        server (str): Name of the server to run reports against.
    
    Returns:
        list[dict]: A list of result dictionaries, each containing:
            - title (str): Report title.
            - sql (str): Executed SQL statement.
            - info: Additional report info (value from config).
            - column_names (list[str]): Column names in the result set.
            - rows (list[tuple]): Query result rows.
    
    Raises:
        ValueError: If a MySQL error or warning occurs while executing reports.
    """
    adhoc_results = []
    db_connect(db, server=server, dictionary=False)  # raises ValueError

    config = get_config()
    for item in config['misc'].get('adhoc_report') or []:
        logging.debug("query: {}".format(item))
        item_sql = item.get('sql')
        if not item_sql:
            logging.warning("Skipping adhoc report item with no SQL: %s", item)
            continue
        result = {
            'title': item.get('title', ''),
            'sql': item_sql,
            'info': item.get('info', ''),
            'rows': [],
            'column_names': [],
        }
        # One broken report (e.g. a table missing on this ProxySQL version)
        # used to raise out of the loop and 500 the whole page; it now carries
        # its own error and the other reports still render.
        try:
            cur = db['cnf']['servers'][server]['cur']
            cur.execute(item_sql)
            result['rows'] = cur.fetchall()
            result['column_names'] = [i[0] for i in cur.description or []]
        except (mysql.connector.Error, mysql.connector.Warning) as e:
            logging.warning("Adhoc report %r failed on %s: %s", result['title'], server, e)
            result['error'] = str(e)
        adhoc_results.append(result)

    return adhoc_results


def get_servers():
    """Names of the configured servers, in config order."""
    try:
        return list(get_config()['servers'] or {})
    except (KeyError, TypeError, ValueError):
        raise ValueError("Cannot get the serverlist from the config file")


def get_default_server():
    """Return the configured default_server, falling back to the first server
    in the servers list if the configured name does not exist."""
    cfg = get_config()
    servers = cfg.get('servers', {})
    configured = cfg.get('global', {}).get('default_server', '')
    if configured in servers:
        return configured
    # Fallback: first server in the config
    first = next(iter(servers), None)
    if first is None:
        raise ValueError("No servers defined in configuration")
    return first

def get_read_only(server):
    """
    Return the configured read-only flag for a named server, falling back to the global default if the server has no explicit setting.
    
    Parameters:
        server (str): Name of the server as listed in the configuration's `servers` section.
    
    Returns:
        bool: The read-only value for the server (`True` or `False`).
    
    Raises:
        ValueError: If the configuration cannot be read or does not contain the expected structure.
    """
    try:
        config = get_config()
        if 'read_only' not in config['servers'][server]:
            read_only = config['global']['read_only']
        else:
            read_only = config['servers'][server]['read_only']
        return read_only
    except Exception:
        raise ValueError("Cannot get read_only status from the config file")



def _run_mysql_cli(server, binary, args, input_sql=None, timeout=30):
    """Run the ``mysql``/``mysqldump`` client against ``server``'s first DSN.

    The password goes in MYSQL_PWD, which keeps it off argv (and out of
    ps/proc). Returns the CompletedProcess; raises subprocess.TimeoutExpired,
    and KeyError/IndexError/TypeError when the server has no usable DSN.
    """
    dsn = get_config()['servers'][server]['dsn'][0]
    cmd = [binary, '-h', str(dsn['host']), '-P', str(dsn['port']), '-u', str(dsn['user'])] + args
    env = os.environ.copy()
    env['MYSQL_PWD'] = str(dsn['passwd'])
    return subprocess.run(
        cmd,
        input=input_sql.encode('utf-8') if input_sql is not None else None,
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def execute_change(db, server, sql):
    """
    Execute a write (INSERT/UPDATE/DELETE, LOAD/SAVE, …) against ``server``.

    We shell out to the mysql CLI because mysql.connector returns wrong
    results for some ProxySQL admin writes (LOAD/SAVE and runtime_* tables).

    Parameters:
        db: Unused — kept so every mdb query helper shares one call shape.
        server (str): Name of the server from configuration to target.
        sql (str): SQL statement(s) to execute.

    Returns:
        str: The client's error output, or an empty string on success.
    """
    logging.debug("Executing change on %s: %s", server, sql)
    try:
        proc = _run_mysql_cli(server, 'mysql', ['main'], input_sql=sql)
    except subprocess.TimeoutExpired:
        logging.error("mysql CLI timed out after 30s for server %s", server)
        return "ERROR: mysql command timed out"
    except (KeyError, IndexError, TypeError) as e:
        logging.error("No usable DSN for server %s: %r", server, e)
        return f"ERROR: no DSN configured for server {server}"

    error_msg = proc.stderr.decode('utf-8', errors='replace')
    if proc.returncode != 0 and not error_msg:
        error_msg = f"mysql CLI exited with status {proc.returncode}"
    if error_msg:
        logging.error(f"SQL execution error: {error_msg}")
    return error_msg


def dump_database(db, server):
    """
    Dump the data of every non-runtime table in ProxySQL's `main` database
    via the mysqldump CLI and return it as SQL text.

    runtime_* tables are excluded by passing the surviving table names as
    explicit positional arguments to mysqldump. The table list is taken
    straight from `SHOW TABLES FROM main` (not the hide_tables-filtered UI
    list) so a backup is never silently missing data.

    Parameters:
        db: Mutable container used to establish or hold a database connection.
        server (str): Name of the server from configuration to target.

    Returns:
        str: The mysqldump output (data-only INSERT statements).

    Raises:
        ValueError: If the table list cannot be fetched or mysqldump fails.
    """
    try:
        db_connect(db, server=server)
        cur = db['cnf']['servers'][server]['cur']
        cur.execute("SHOW TABLES FROM main")
        tables = [row['tables'] for row in cur.fetchall()
                  if not row['tables'].startswith('runtime_')]
        cur.close()
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        raise ValueError(e)

    if not tables:
        raise ValueError("no tables found to dump in database 'main'")

    try:
        proc = _run_mysql_cli(server, 'mysqldump', [
            '--skip-comments',
            '--skip-set-charset',
            '--skip-tz-utc',
            '--compact',
            '--no-tablespaces',
            '--no-create-info',
            '--no-create-db',
            '--skip-triggers',
            # no --network-timeout: MariaDB's mysqldump (used on arm64 images)
            # doesn't support it, and the subprocess timeout covers hangs.
            'main',
        ] + tables, timeout=60)
    except subprocess.TimeoutExpired:
        logging.error("mysqldump timed out after 60s for server %s", server)
        raise ValueError("mysqldump timed out")

    if proc.returncode != 0:
        error_msg = proc.stderr.decode('utf-8', errors='replace').strip() \
            or f"mysqldump exited with status {proc.returncode}"
        logging.error(f"mysqldump error: {error_msg}")
        raise ValueError(error_msg)

    return proc.stdout.decode('utf-8', errors='replace')


def get_table_schema(db, server, database, table_name):
    """
    Extract comprehensive schema information from a table via ProxySQL admin interface using SHOW CREATE TABLE.

    Args:
        db: Database connection dict
        server: Server name from config
        database: Database name (usually 'main' for ProxySQL)
        table_name: Name of the table to analyze

    Returns:
        dict: Structured schema information with the following format:
        {
            'table_name': 'table_name',
            'columns': {
                'column_name': {
                    'type': 'VARCHAR(255)',
                    'nullable': True/False,
                    'default': 'default_value',
                    'check_constraint': 'value1 OR value2 OR value3'  # if exists
                }
            }
        }
    """
    result = {
        'table_name': table_name,
        'columns': {}
    }

    try:
        # Connect to the database through ProxySQL admin interface
        db_connect(db, server=server, dictionary=True)

        logging.debug(f"Extracting schema for table: {database}.{table_name} via server: {server}")

        # Use SHOW CREATE TABLE to get the full table definition
        query = f"SHOW CREATE TABLE {_quote_ident(database)}.{_quote_ident(table_name)}"
        db['cnf']['servers'][server]['cur'].execute(query)
        create_table_result = db['cnf']['servers'][server]['cur'].fetchone()

        if not create_table_result:
            raise ValueError(f"Table '{table_name}' not found in database '{database}'")

        create_table_sql = _create_table_sql(create_table_result)

        logging.debug(f"CREATE TABLE statement:\n{create_table_sql}")

        # Extract column definitions from CREATE TABLE
        columns_info = parse_column_definitions(create_table_sql)

        # Process each column
        for col_name, col_info in columns_info.items():
            # Build column info from parsed CREATE TABLE
            column_info = {
                'type': col_info.get('type', 'TEXT'),
                'nullable': col_info.get('nullable', True),
                'default': col_info.get('default')
            }

            # Add CHECK constraint if exists
            if 'check_constraint' in col_info:
                column_info['check_constraint'] = col_info['check_constraint']

            result['columns'][col_name] = column_info

        logging.debug(f"Successfully extracted schema for table '{table_name}'")

    except (mysql.connector.Error, mysql.connector.Warning) as e:
        logging.error(f"MySQL error: {e}")
        raise ValueError(f"MySQL error while extracting schema: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise ValueError(f"Unexpected error while extracting schema: {e}")
    finally:
        _safe_close_conn(db, server)

    return result


def _split_top_level_commas(s):
    """Split *s* on commas that are at parenthesis depth zero."""
    parts = []
    depth = 0
    start = 0
    for i, ch in enumerate(s):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(depth - 1, 0)
        elif ch == ',' and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts


def _parse_inline_primary_keys(create_table_sql):
    """Extract PK columns declared inline as ``colname TYPE ... PRIMARY KEY``.

    ProxySQL (SQLite-backed) uses this form for many admin tables
    (``mysql_query_rules``, ``global_variables``, ``scheduler``, etc.)
    where the classic ``PRIMARY KEY (col, ...)`` trailing clause is absent.
    """
    first = create_table_sql.find('(')
    last = create_table_sql.rfind(')')
    if first == -1 or last <= first:
        return []
    body = create_table_sql[first + 1:last]

    reserved = {'PRIMARY', 'UNIQUE', 'KEY', 'CHECK',
                'CONSTRAINT', 'FOREIGN', 'INDEX'}
    pk_cols = []
    for defn in _split_top_level_commas(body):
        defn = defn.strip()
        if not defn:
            continue
        if not re.search(r'\bPRIMARY\s+KEY\b(?!\s*\()', defn, re.IGNORECASE):
            continue
        m = re.match(r'[`"\[]?([A-Za-z_][\w$]*)[`"\]]?\b', defn)
        if not m:
            continue
        first_ident = m.group(1)
        if first_ident.upper() in reserved:
            continue
        pk_cols.append(first_ident)
    return pk_cols


def _create_table_sql(row):
    """Pull the CREATE TABLE text out of a SHOW CREATE TABLE result row.

    The row is a dict or a tuple depending on the cursor, and the column label
    varies in casing by server, so match case-insensitively before falling back
    to positional access. Returns None when the text cannot be located.
    """
    if isinstance(row, dict):
        for key, value in row.items():
            if 'create table' in str(key).lower():
                return value
        return None
    try:
        return row[1]
    except (IndexError, TypeError, KeyError):
        return None


def get_primary_key_columns(db, server, database, table_name):
    """
    Return the primary key column names defined for the specified table.

    Parses the table's CREATE TABLE SQL and extracts the PRIMARY KEY column list.

    Returns:
        list: Primary key column names in definition order, or an empty list if
        the table genuinely has no primary key.
        None: If the lookup failed. Callers must not treat this as "no primary
        key" — the fallback for that case is the caller-supplied column list,
        which would silently widen an UPDATE/DELETE WHERE clause.
    """
    try:
        db_connect(db, server=server, dictionary=True)

        # Get the CREATE TABLE statement
        query = f"SHOW CREATE TABLE {_quote_ident(database)}.{_quote_ident(table_name)}"
        db['cnf']['servers'][server]['cur'].execute(query)
        create_table_result = db['cnf']['servers'][server]['cur'].fetchone()

        if not create_table_result:
            logging.warning(f"Table '{table_name}' not found")
            return None

        create_table_sql = _create_table_sql(create_table_result)
        if not create_table_sql:
            logging.warning(f"Could not read CREATE TABLE for '{table_name}'")
            return None

        # Block form: PRIMARY KEY (column1, column2, ...)
        pk_pattern = r'PRIMARY\s+KEY\s*\(([^)]+)\)'
        match = re.search(pk_pattern, create_table_sql, re.IGNORECASE)
        if match:
            pk_columns = [col.strip().strip('`"[]')
                          for col in match.group(1).split(',')]
            logging.debug(f"Primary key columns for {table_name}: {pk_columns}")
            return pk_columns

        # Inline form: `col TYPE ... PRIMARY KEY` — common in ProxySQL's
        # SQLite-style admin schemas, and missed by the block-form regex above.
        inline_pk = _parse_inline_primary_keys(create_table_sql)
        if inline_pk:
            logging.debug(f"Primary key columns for {table_name}: {inline_pk}")
            return inline_pk

        logging.warning(f"No primary key found for table {table_name}")
        return []

    except Exception as e:
        logging.error(f"Error extracting primary key for {table_name}: {e}")
        return None
    finally:
        _safe_close_conn(db, server)


def parse_column_definitions(create_table_sql):
    """
    Extracts column definitions from a CREATE TABLE statement.
    
    Parameters:
        create_table_sql (str): The full CREATE TABLE SQL text.
    
    Returns:
        dict: Mapping of column name to a metadata dict for each column. Each metadata dict contains keys:
            - 'name' (str): Column name.
            - 'type' (str): Column data type (as parsed).
            - 'nullable' (bool): True if the column allows NULL, False otherwise.
            - 'default' (str|None): The parsed DEFAULT value, or None if not present.
            - 'check_constraint' (str, optional): CHECK constraint expression if present.
    """
    columns = {}

    try:
        # Extract the column definitions between CREATE TABLE (...) )
        # Find the content between the first ( and the last )
        start = create_table_sql.find('(')
        end = create_table_sql.rfind(')')
        if start == -1 or end == -1:
            logging.warning("Could not find column definitions in CREATE TABLE")
            return columns

        column_defs_text = create_table_sql[start+1:end]

        # Split by comma, but be careful with commas inside parentheses
        column_defs = split_sql_columns(column_defs_text)

        for col_def in column_defs:
            col_def = col_def.strip()
            if not col_def or col_def.upper().startswith('PRIMARY') or col_def.upper().startswith('KEY') or col_def.upper().startswith('UNIQUE'):
                continue

            # Parse the column definition
            col_info = parse_column_definition(col_def)
            if col_info:
                columns[col_info['name']] = col_info

    except Exception as e:
        logging.error(f"Error parsing column definitions: {e}")

    return columns


def split_sql_columns(text):
    """Split SQL column definitions on top-level commas, dropping blanks."""
    return [c.strip() for c in _split_top_level_commas(text) if c.strip()]


def parse_column_definition(col_def):
    """
    Parse a single column definition from a CREATE TABLE statement.
    
    Parameters:
        col_def (str): The raw column definition fragment (e.g. "`id` INT NOT NULL AUTO_INCREMENT").
    
    Returns:
        dict or None: A mapping with the following keys when parsing succeeds:
            - name (str): Column identifier without quoting characters.
            - type (str): Declared column type including any parenthesized size/precision (e.g. "varchar(255)").
            - nullable (bool): `False` if the column is declared `NOT NULL`, `True` otherwise.
            - default (str or None): The default value as a string with surrounding quotes removed, or `None` if no DEFAULT is present.
            - check_constraint (str or None): The CHECK expression contents (without the outer parentheses) if a CHECK constraint is present, otherwise `None`.
        Returns `None` if the input cannot be parsed.
    """
    try:
        col_def = col_def.strip()

        # Extract column name (first word)
        parts = col_def.split(None, 1)
        if len(parts) < 2:
            return None

        col_name = parts[0].strip('`"[]')
        rest = parts[1]

        # Extract column type (up to first keyword like NULL, NOT, DEFAULT, etc.)
        type_match = re.match(r'^(\w+(?:\([^)]+\))?)', rest)
        if not type_match:
            return None

        col_type = type_match.group(1).strip()
        rest_after_type = rest[len(col_type):].strip()

        # Check for NULL/NOT NULL
        nullable = True
        if re.search(r'\bNOT\s+NULL\b', rest_after_type, re.IGNORECASE):
            nullable = False
        elif re.search(r'\bNULL\b', rest_after_type, re.IGNORECASE):
            nullable = True

        # Extract DEFAULT value
        default_match = re.search(r'\bDEFAULT\s+(\'\'|\'[^\']*\'|"[^"]*"|\w+|\d+)', rest_after_type, re.IGNORECASE)
        default_value = None
        if default_match:
            default_value = default_match.group(1).strip('\'"')

        # Check for CHECK constraints
        check_constraint = None
        check_match = re.search(r'\bCHECK\s*\(', rest_after_type, re.IGNORECASE)
        if check_match:
            # Find the matching closing parenthesis
            start_pos = check_match.end() - 1  # Position of the opening '('
            paren_count = 1
            pos = start_pos + 1
            while pos < len(rest_after_type) and paren_count > 0:
                if rest_after_type[pos] == '(':
                    paren_count += 1
                elif rest_after_type[pos] == ')':
                    paren_count -= 1
                pos += 1

            if paren_count == 0:
                # Extract the constraint without outer parentheses
                check_constraint = rest_after_type[start_pos + 1:pos - 1].strip()

        return {
            'name': col_name,
            'type': col_type,
            'nullable': nullable,
            'default': default_value,
            'check_constraint': check_constraint
        }

    except Exception as e:
        logging.error(f"Error parsing column definition '{col_def}': {e}")
        return None


def _sql_literal(value):
    """Render a value as a SQL literal: NULL, or a single-quoted string with
    embedded quotes doubled (ProxySQL's admin is SQLite-based, where ''
    is the only string escape)."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _pk_where(db, server, database, table, pk_values, extra_columns=()):
    """Build the WHERE clause that identifies one row by its primary key.

    Every caller-supplied column (``pk_values`` keys and ``extra_columns``) is
    validated against the live schema first. Returns ``(where_sql, None)`` or
    ``(None, error_message)``.
    """
    if not pk_values:
        return None, 'No primary key values provided'

    allowed_columns = set(get_column_names(db, server, database, table))
    for col in set(pk_values) | set(extra_columns):
        if col not in allowed_columns:
            return None, f'Unknown column: {col!r}'

    # None means the lookup failed — falling back to the caller's columns
    # there would widen the WHERE clause on a transient error, so refuse.
    pk_cols = get_primary_key_columns(db, server, database, table)
    if pk_cols is None:
        return None, 'Could not determine the primary key for this table'
    if not pk_cols:
        pk_cols = list(pk_values)  # no PK: use whatever was sent
    missing = [col for col in pk_cols if col not in pk_values]
    if missing:
        # A missing key column used to become `col IS NULL`, which matches
        # nothing on a NOT NULL key — the call reported success and changed
        # zero rows. A key column that is really NULL must be sent as null.
        return None, f'Missing primary key column(s): {", ".join(missing)}'

    conditions = [
        f"{_quote_ident(col)} IS NULL" if pk_values[col] is None
        else f"{_quote_ident(col)} = {_sql_literal(pk_values[col])}"
        for col in pk_cols
    ]
    return " AND ".join(conditions), None


def _run_row_change(db, server, sql):
    """Execute a row mutation and wrap the outcome in the row-API result shape."""
    logging.debug("Row change SQL: %s", sql)
    error = execute_change(db, server, sql)
    return {'success': not error, 'error': error or None}


def update_row(db, server, database, table, pk_values, data):
    """
    Update the row identified by ``pk_values`` (every PK column; None matches
    IS NULL) with the ``data`` mapping (None sets NULL).

    Returns:
        dict: ``{'success': bool, 'error': str | None}``.
    """
    try:
        where, error = _pk_where(db, server, database, table, pk_values, extra_columns=data)
        if error:
            return {'success': False, 'error': error}
        if not data:
            return {'success': False, 'error': 'No columns to update'}
        set_clause = ', '.join(f"{_quote_ident(col)} = {_sql_literal(val)}"
                               for col, val in data.items())
        return _run_row_change(db, server, "UPDATE {}.{} SET {} WHERE {}".format(
            _quote_ident(database), _quote_ident(table), set_clause, where))
    except Exception as e:
        logging.error(f"Error in update_row: {e}")
        return {'success': False, 'error': str(e)}


def delete_row(db, server, database, table, pk_values):
    """
    Delete the row identified by ``pk_values`` (every PK column; None matches
    IS NULL). Tables without a primary key match on the columns sent.

    Returns:
        dict: ``{'success': bool, 'error': str | None}``.
    """
    try:
        where, error = _pk_where(db, server, database, table, pk_values)
        if error:
            return {'success': False, 'error': error}
        return _run_row_change(db, server, "DELETE FROM {}.{} WHERE {}".format(
            _quote_ident(database), _quote_ident(table), where))
    except Exception as e:
        logging.error(f"Error in delete_row: {e}")
        return {'success': False, 'error': str(e)}


def insert_row(db, server, database, table, column_names, data):
    """
    Insert a row. Columns in ``column_names`` (validated against the live
    schema; ``variable_name`` is ignored) whose ``data`` value is missing or
    None are omitted so the column's DEFAULT applies.

    Returns:
        dict: ``{'success': bool, 'error': str | None}``.
    """
    try:
        allowed_columns = set(get_column_names(db, server, database, table))
        columns = [col for col in column_names if col != 'variable_name']
        for col in columns:
            if col not in allowed_columns:
                return {'success': False, 'error': f'Unknown column: {col!r}'}

        insert_columns = [col for col in columns if data.get(col) is not None]
        if not insert_columns:
            return {'success': False, 'error': 'No columns to insert'}

        return _run_row_change(db, server, "INSERT INTO {}.{} ({}) VALUES ({})".format(
            _quote_ident(database), _quote_ident(table),
            ', '.join(_quote_ident(c) for c in insert_columns),
            ', '.join(_sql_literal(data[c]) for c in insert_columns)))
    except Exception as e:
        logging.error(f"Error in insert_row: {e}")
        return {'success': False, 'error': str(e)}

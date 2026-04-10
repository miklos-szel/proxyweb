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
import logging
import os
import yaml
import subprocess
import re
import json
from datetime import datetime

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

HISTORY_DIR = os.path.join(os.path.dirname(__file__), 'data', 'history')


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


def append_query_history(server, sql, user='admin'):
    """
    Append a SQL query entry to the per-server JSON history file.
    
    If `server` is invalid (fails validation), the function is a no-op. The function ensures the history directory exists and writes the updated history atomically.
    
    Parameters:
        server (str): Server name used to select the history file (safe name required).
        sql (str): The SQL statement to record.
        user (str): Username associated with the query entry; defaults to 'admin'.
    """
    if not _valid_history_server(server):
        return
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f'{server}.json')
    entry = {"sql": sql, "timestamp": datetime.now().isoformat(), "user": user}
    history = load_query_history(server)
    history.append(entry)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, path)


def load_query_history(server, limit=None):
    """
    Load stored query history for a server.
    
    Returns the server's history entries in chronological order (oldest first, most recent last). If `limit` is provided, returns only the last `limit` entries. Returns an empty list when the server name is invalid or no history exists.
    
    Parameters:
        server (str): Server identifier (validated for safe file-path use).
        limit (int | None): If set, return only the most recent `limit` entries.
    
    Returns:
        list: A list of history entry dictionaries (each typically contains keys such as `sql`, `ts`/`timestamp`, and `user`).
    """
    if not _valid_history_server(server):
        return []
    path = os.path.join(HISTORY_DIR, f'{server}.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r') as f:
            history = json.load(f)
    except (json.JSONDecodeError, ValueError):
        logging.warning(f"Corrupted history file for server '{server}', resetting")
        return []
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


def _quote_ident(name):
    """
    Backtick-quote a SQL identifier, doubling any embedded backticks.
    
    Parameters:
        name (str): Identifier name to quote.
    
    Returns:
        quoted (str): The input wrapped in backticks with any internal backticks doubled.
    """
    return '`' + name.replace('`', '``') + '`'


sql_get_databases = "show databases"

def get_config(config="config/config.yml"):
    """
    Load and parse a YAML configuration file into a Python dictionary.
    
    Parameters:
        config (str): Path to the YAML configuration file.
    
    Returns:
        dict: Parsed configuration dictionary.
    
    Raises:
        ValueError: If the file cannot be opened or the YAML cannot be parsed.
    """
    logging.debug("Using file: %s" % (config))
    try:
        with open(config, 'r') as yml:
            cfg = yaml.safe_load(yml)
        cfg = _apply_env_overrides(cfg)
        return cfg
    except Exception as e:
        raise ValueError("Error opening or parsing the file: %s" % config)


def _apply_env_overrides(cfg):
    """Override config values with PROXYWEB_* environment variables when set."""
    if not cfg:
        return cfg

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
            logging.info("Config override: %s.%s from env %s", section, key, env_key)

    # Per-server DSN overrides: PROXYWEB_SERVER_<NAME>_{USER,PASSWORD,HOST,PORT,DATABASE}
    _DSN_FIELD_MAP = {
        'USER': 'user',
        'PASSWORD': 'passwd',
        'HOST': 'host',
        'PORT': 'port',
        'DATABASE': 'db',
    }
    for server_name, server_cfg in cfg.get('servers', {}).items():
        prefix = f"PROXYWEB_SERVER_{server_name.upper()}_"
        overrides = {}
        for env_suffix, dsn_key in _DSN_FIELD_MAP.items():
            value = os.environ.get(prefix + env_suffix)
            if value is not None:
                overrides[dsn_key] = int(value) if dsn_key == 'port' else value

        if overrides:
            logging.info("Config override: server %s DSN fields %s from env",
                         server_name, list(overrides.keys()))
            for dsn in server_cfg.get('dsn', []):
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



def format_yaml_value(value):
    """
    Format a Python value into a YAML-friendly scalar string.
    
    Converts common Python scalar types to a YAML-safe string representation:
    - None becomes an empty quoted string `""`.
    - booleans become `true` or `false`.
    - integers and floats are converted via `str`.
    - strings are returned unquoted unless they contain characters that require quoting, in which case they are wrapped in double quotes.
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
        # Check if string contains special characters that need quotes
        if any(char in value for char in ['[', ']', ':', '{', '}', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '`']):
            return f'"{value}"'
        return value
    if isinstance(value, list):
        return f'"{value}"'
    return f'"{value}"'


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
        raise ValueError(f"Invalid YAML syntax: {str(e)}")


def validate_config_shape(cfg):
    """
    Validate that a configuration mapping contains required top-level sections and keys.
    
    Parameters:
        cfg (dict): Configuration mapping parsed from YAML to validate.
    
    Raises:
        ValueError: If `cfg` is not a mapping.
        ValueError: If a required top-level section is missing.
        ValueError: If a required top-level section exists but is not a mapping.
        ValueError: If a required key is missing from a section (format: 'section.key').
    
    Notes:
        Required sections and keys enforced:
          - auth: 'admin_user', 'admin_password'
          - global: 'default_server'
          - flask: 'SECRET_KEY'
          - servers: (must be a mapping)
          - misc: (must be a mapping)
    """
    if not isinstance(cfg, dict):
        raise ValueError("Config must be a YAML mapping")
    required = {
        'auth': ['admin_user', 'admin_password'],
        'global': ['default_server'],
        'flask': ['SECRET_KEY'],
        'servers': [],
        'misc': [],
    }
    for section, keys in required.items():
        if section not in cfg:
            raise ValueError(f"Missing required config section: '{section}'")
        if not isinstance(cfg[section], dict):
            raise ValueError(f"Config section '{section}' must be a mapping")
        for key in keys:
            if key not in cfg[section]:
                raise ValueError(f"Missing required key: '{section}.{key}'")


def form_data_to_yaml(form_data):
    """
    Builds a full configuration YAML string from submitted form data.
    
    Reads expected form fields and reconstructs the configuration structure (global, servers, auth, flask, misc), normalizing boolean flags, collecting repeated/numbered entries (DSNs, hide_tables, apply/update/adhoc config items), and converting escaped newlines ("\\n") into real newlines for SQL/info blocks. Ensures the resulting config always includes 'servers' and 'misc' keys (possibly empty) and prepends a generated header with a timestamp.
    
    Parameters:
        form_data (dict): Mapping of form field names to string values as submitted by the UI.
    
    Returns:
        str: Complete YAML document (including header comment) representing the reconstructed configuration.
    """
    config = {
        'global': {},
        'servers': {},
        'auth': {},
        'flask': {},
        'misc': {}
    }

    # Process global section
    if 'global_default_server' in form_data:
        config['global']['default_server'] = form_data['global_default_server']

    # Handle read_only - checkbox unchecked means false (checkbox not submitted when unchecked)
    if 'global_read_only' in form_data:
        config['global']['read_only'] = form_data['global_read_only'].lower() == 'true'
    else:
        config['global']['read_only'] = False

    if 'global_sqlite_db_path' in form_data:
        config['global']['sqlite_db_path'] = form_data['global_sqlite_db_path']

    # Process hide_tables
    hide_tables = []
    for key, value in form_data.items():
        if key.startswith('global_hide_tables_') and value:
            hide_tables.append(value)
    if hide_tables:
        config['global']['hide_tables'] = hide_tables

    # Process servers section
    server_count = int(form_data.get('server_count', 0))
    for i in range(server_count):
        server_name = form_data.get(f'server_{i}_name')
        if not server_name:
            logging.warning(f"form_data_to_yaml: skipping server index {i} — no name provided")
            continue

        server_config = {'dsn': []}

        # DSN array
        dsn_count = int(form_data.get(f'server_{i}_dsn_count', 0))
        for j in range(dsn_count):
            dsn = {}
            if form_data.get(f'server_{i}_dsn_{j}_host'):
                dsn['host'] = form_data[f'server_{i}_dsn_{j}_host']
            if form_data.get(f'server_{i}_dsn_{j}_user'):
                dsn['user'] = form_data[f'server_{i}_dsn_{j}_user']
            if form_data.get(f'server_{i}_dsn_{j}_passwd'):
                dsn['passwd'] = form_data[f'server_{i}_dsn_{j}_passwd']
            if form_data.get(f'server_{i}_dsn_{j}_port'):
                dsn['port'] = form_data[f'server_{i}_dsn_{j}_port']
            if form_data.get(f'server_{i}_dsn_{j}_db'):
                dsn['db'] = form_data[f'server_{i}_dsn_{j}_db']

            if dsn:
                server_config['dsn'].append(dsn)

        # Optional read_only override
        server_read_only = form_data.get(f'server_{i}_read_only_override')
        if server_read_only:
            server_config['read_only'] = server_read_only.lower() == 'true'

        # Optional hide_tables
        server_hide_tables = []
        for key, value in form_data.items():
            if key.startswith(f'server_{i}_hide_tables_') and value:
                server_hide_tables.append(value)
        if server_hide_tables:
            server_config['hide_tables'] = server_hide_tables

        if server_config['dsn']:
            config['servers'][server_name] = server_config

    # Process auth section
    if 'auth_admin_user' in form_data:
        config['auth']['admin_user'] = form_data['auth_admin_user']

    if 'auth_admin_password' in form_data:
        config['auth']['admin_password'] = form_data['auth_admin_password']

    if 'auth_readonly_user' in form_data:
        config['auth']['readonly_user'] = form_data['auth_readonly_user']

    if 'auth_readonly_password' in form_data:
        config['auth']['readonly_password'] = form_data['auth_readonly_password']

    # Process flask section
    if 'flask_SECRET_KEY' in form_data:
        config['flask']['SECRET_KEY'] = form_data['flask_SECRET_KEY']

    if 'flask_SEND_FILE_MAX_AGE_DEFAULT' in form_data:
        config['flask']['SEND_FILE_MAX_AGE_DEFAULT'] = form_data['flask_SEND_FILE_MAX_AGE_DEFAULT']

    if 'flask_TEMPLATES_AUTO_RELOAD' in form_data:
        config['flask']['TEMPLATES_AUTO_RELOAD'] = form_data['flask_TEMPLATES_AUTO_RELOAD']

    # Process misc section
    misc = {}

    # Apply config array
    apply_config = []
    apply_config_count = int(form_data.get('misc_apply_config_count', 0))
    for i in range(apply_config_count):
        item = {}
        if form_data.get(f'misc_apply_config_{i}_title'):
            item['title'] = form_data[f'misc_apply_config_{i}_title']
        if form_data.get(f'misc_apply_config_{i}_info'):
            item['info'] = form_data[f'misc_apply_config_{i}_info'].replace('\\n', '\n')
        if form_data.get(f'misc_apply_config_{i}_sql'):
            item['sql'] = form_data[f'misc_apply_config_{i}_sql'].replace('\\n', '\n')
        if item:
            apply_config.append(item)
    if apply_config:
        misc['apply_config'] = apply_config

    # Update config array
    update_config = []
    update_config_count = int(form_data.get('misc_update_config_count', 0))
    for i in range(update_config_count):
        item = {}
        if form_data.get(f'misc_update_config_{i}_title'):
            item['title'] = form_data[f'misc_update_config_{i}_title']
        if form_data.get(f'misc_update_config_{i}_info'):
            item['info'] = form_data[f'misc_update_config_{i}_info'].replace('\\n', '\n')
        if form_data.get(f'misc_update_config_{i}_sql'):
            item['sql'] = form_data[f'misc_update_config_{i}_sql'].replace('\\n', '\n')
        if item:
            update_config.append(item)
    if update_config:
        misc['update_config'] = update_config

    # Adhoc report array
    adhoc_report = []
    adhoc_report_count = int(form_data.get('misc_adhoc_report_count', 0))
    for i in range(adhoc_report_count):
        item = {}
        if form_data.get(f'misc_adhoc_report_{i}_title'):
            item['title'] = form_data[f'misc_adhoc_report_{i}_title']
        if form_data.get(f'misc_adhoc_report_{i}_info'):
            item['info'] = form_data[f'misc_adhoc_report_{i}_info']
        if form_data.get(f'misc_adhoc_report_{i}_sql'):
            item['sql'] = form_data[f'misc_adhoc_report_{i}_sql']
        if item:
            adhoc_report.append(item)
    if adhoc_report:
        misc['adhoc_report'] = adhoc_report

    if misc:
        config['misc'] = misc

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


def db_connect(db, server, buffered=False, dictionary=True):
    """
    Establishes a MySQL connection for the given server and stores the loaded configuration, connection, and cursor in the provided `db` dictionary.

    Parameters:
        db (dict): Mutable mapping where configuration (`'cnf'`), connection (`'conn'`) and cursor (`'cur'`) will be stored.
        server (str): Key identifying the server entry inside the loaded configuration's `servers` section.
        buffered (bool): If True, create a buffered cursor.
        dictionary (bool): If True, create a cursor that returns rows as dictionaries.

    Raises:
        ValueError: If a MySQL connector error or warning occurs while connecting or creating the cursor.
    """
    try:
        db['cnf'] = get_config()

        config = db['cnf']['servers'][server]['dsn'][0]
        logging.debug(db['cnf']['servers'][server]['dsn'][0])
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
                logging.warning("Server %s does not support SET @@session.autocommit, skipping: %s", server, err)
                if not conn.is_connected():
                    raise
            else:
                raise
        db['cnf']['servers'][server]['conn'] = conn

        if conn.is_connected():
            logging.debug("Connected successfully to %s as %s db=%s" % (
                config['host'],
                config['user'],
                config['db']))

        conn.get_warnings = True

        db['cnf']['servers'][server]['cur'] = conn.cursor(buffered=buffered,
                                                          dictionary=dictionary)
        logging.debug("buffered: %s, dictionary: %s" % (buffered, dictionary))

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
        table_exception_list = []

        if 'hide_tables' not in db['cnf']['servers'][server]:
            #it there is a global hide_tables defined and there is no local one:
            if len(db['cnf']['global']['hide_tables']) > 0:
                table_exception_list = db['cnf']['global']['hide_tables']
        else:
                table_exception_list = db['cnf']['servers'][server]['hide_tables']

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
                - total_changes (dict) with keys 'added', 'modified', 'deleted'
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
    try:
        # Get config to access hide_tables and skip_variables
        config = get_config()
        if server is None:
            server = get_default_server()
        # Use per-server hide_tables if defined, otherwise fall back to global
        # (matches the logic in get_all_dbs_and_tables)
        server_config = config.get('servers', {}).get(server, {})
        if 'hide_tables' in server_config:
            hide_tables = server_config['hide_tables']
        else:
            hide_tables = config.get('global', {}).get('hide_tables', [])
        skip_variables = config.get('global', {}).get('config_diff_skip_variable', [])

        diff_result = {
            'summary': {
                'total_tables': 0,
                'tables_with_differences': 0,
                'total_changes': {
                    'added': 0,
                    'modified': 0,
                    'deleted': 0
                }
            },
            'tables': [],
            'config_diff_skip_variable': skip_variables
        }

        # Connect to main database to get all tables
        query_db = {}
        db_connect(query_db, server=server, dictionary=False)
        query_db['cnf']['servers'][server]['conn'].database = 'main'

        # Get all tables from main database
        query_db['cnf']['servers'][server]['cur'].execute("SHOW TABLES")
        all_tables = [table[0] for table in query_db['cnf']['servers'][server]['cur'].fetchall()]

        # Get base table names (without runtime_ prefix)
        tables_to_diff = []
        for table in all_tables:
            # Skip runtime_ tables (we'll use them for runtime comparison)
            if table.startswith('runtime_'):
                continue

            # Skip tables that match hide_patterns
            if should_hide_table(table, hide_tables):
                continue

            # Skip internal tables
            if table in ['sqlite_sequence']:
                continue

            # Only include ProxySQL configuration tables
            if any(table.startswith(prefix) for prefix in ['mysql_', 'pgsql_', 'admin_', 'global_', 'scheduler', 'restapi']):
                tables_to_diff.append(table)

        query_db['cnf']['servers'][server]['conn'].close()

        for table_name in tables_to_diff:
            table_diff = {
                'table_name': table_name,
                'databases': {},
                'differences': [],
                'stats': {
                    'disk_rows': 0,
                    'memory_rows': 0,
                    'runtime_rows': 0,
                    'has_differences': False
                }
            }

            # Define queries for each layer using correct naming convention
            queries = {
                'disk': f"SELECT * FROM disk.{table_name}",
                'memory': f"SELECT * FROM main.{table_name}",
                'runtime': f"SELECT * FROM main.runtime_{table_name}"
            }

            # Query each layer
            for layer_name, query in queries.items():
                try:
                    query_db = {}
                    db_connect(query_db, server=server, dictionary=False)

                    # Query and get results
                    query_db['cnf']['servers'][server]['cur'].execute(query)
                    rows = query_db['cnf']['servers'][server]['cur'].fetchall()

                    # Convert rows to dictionaries for comparison
                    if rows:
                        column_names = [desc[0] for desc in query_db['cnf']['servers'][server]['cur'].description]
                        dict_rows = [dict(zip(column_names, row)) for row in rows]
                    else:
                        column_names = []
                        dict_rows = []

                    table_diff['databases'][layer_name] = {
                        'row_count': len(dict_rows),
                        'data': dict_rows,
                        'column_order': column_names
                    }
                    table_diff['stats'][f'{layer_name}_rows'] = len(dict_rows)

                    query_db['cnf']['servers'][server]['conn'].close()

                except Exception as e:
                    # Table might not exist in all layers
                    try:
                        query_db['cnf']['servers'][server]['conn'].close()
                    except Exception:
                        pass
                    table_diff['databases'][layer_name] = {
                        'row_count': 0,
                        'data': [],
                        'column_order': [],
                        'error': str(e)
                    }
                    table_diff['stats'][f'{layer_name}_rows'] = 0

            # Calculate differences
            has_diffs = False
            disk_data = table_diff['databases'].get('disk', {}).get('data', [])
            memory_data = table_diff['databases'].get('memory', {}).get('data', [])
            runtime_data = table_diff['databases'].get('runtime', {}).get('data', [])

            # Build hash to row mapping for each layer
            def build_hash_map(data):
                """
                Build a mapping from a deterministic serialized representation of each row to the original row.
                
                Parameters:
                    data (iterable): An iterable of JSON-serializable rows (e.g., dicts or lists).
                
                Returns:
                    dict: A dictionary whose keys are deterministic serialized representations of each row (stable key order) and whose values are the original row objects.
                """
                hash_map = {}
                for row in data:
                    row_hash = json.dumps(row, sort_keys=True)
                    hash_map[row_hash] = row
                return hash_map

            disk_map = build_hash_map(disk_data)
            memory_map = build_hash_map(memory_data)
            runtime_map = build_hash_map(runtime_data)

            disk_hashes = set(disk_map.keys())
            memory_hashes = set(memory_map.keys())
            runtime_hashes = set(runtime_map.keys())

            # Find differences between disk and memory
            only_in_disk = []
            for row_hash in (disk_hashes - memory_hashes):
                only_in_disk.append(disk_map[row_hash])

            only_in_memory = []
            for row_hash in (memory_hashes - disk_hashes):
                only_in_memory.append(memory_map[row_hash])

            # Find differences between memory and runtime
            only_in_memory_not_runtime = []
            for row_hash in (memory_hashes - runtime_hashes):
                only_in_memory_not_runtime.append(memory_map[row_hash])

            only_in_runtime = []
            for row_hash in (runtime_hashes - memory_hashes):
                only_in_runtime.append(runtime_map[row_hash])

            # Store detailed differences
            table_diff['differences'] = {
                'disk_vs_memory': {
                    'only_in_disk': only_in_disk,
                    'only_in_memory': only_in_memory
                },
                'memory_vs_runtime': {
                    'only_in_memory': only_in_memory_not_runtime,
                    'only_in_runtime': only_in_runtime
                }
            }

            # Determine if there are differences
            if only_in_disk or only_in_memory or only_in_memory_not_runtime or only_in_runtime:
                has_diffs = True

            table_diff['stats']['has_differences'] = has_diffs

            if has_diffs:
                diff_result['summary']['tables_with_differences'] += 1

            diff_result['summary']['total_tables'] += 1
            diff_result['tables'].append(table_diff)

        return diff_result

    except Exception as e:
        logging.error(f"Error in get_config_diff: {e}")
        raise


def get_table_content(db, server, database, table):
    """
    Return the rows, column names, and miscellaneous config for a specific table.
    
    Retrieves all rows from the given database.table ordered by the first column, records the result rows and column names, and includes the global 'misc' section from the loaded configuration.
    
    Parameters:
        db (dict): Application DB context dict used by db_connect to store connection/cursor.
        server (str): Server name as defined in the configuration.
        database (str): Database name containing the table.
        table (str): Table name to fetch.
    
    Returns:
        content (dict): Dictionary with keys:
            - 'rows' (list of tuples): All table rows returned by the query.
            - 'column_names' (list of str): Column names in result order.
            - 'misc' (dict): The 'misc' section from the loaded configuration.
    
    Raises:
        ValueError: If a MySQL connector error or warning occurs while querying.
    """
    content = {}
    try:
        logging.debug("server: {} - db: {} - table:{}".format(server, database, table))
        db_connect(db, server=server, dictionary=False)
        string = f"SELECT * FROM {_quote_ident(database)}.{_quote_ident(table)} ORDER BY 1"
        logging.debug("query: {}".format(string))

        db['cnf']['servers'][server]['cur'].execute(string)

        content['rows'] = db['cnf']['servers'][server]['cur'].fetchall()
        content['column_names'] = [i[0] for i in db['cnf']['servers'][server]['cur'].description]
        content['misc'] = get_config()['misc']
        return content
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        db['cnf']['servers'][server]['conn'].close()
        raise ValueError(e)


def get_table_metadata(db, server, database, table):
    """Return column names, row count, and misc config without fetching row data."""
    try:
        db_connect(db, server=server, dictionary=False)
        cur = db['cnf']['servers'][server]['cur']
        cur.execute(f"SELECT * FROM {_quote_ident(database)}.{_quote_ident(table)} LIMIT 0")
        column_names = [i[0] for i in cur.description]
        cur.fetchall()  # consume empty result set before next query
        cur.execute(f"SELECT COUNT(*) FROM {_quote_ident(database)}.{_quote_ident(table)}")
        row_count = cur.fetchone()[0]
        return {
            'rows': [],
            'column_names': column_names,
            'row_count': row_count,
            'misc': get_config()['misc'],
            'server_side': True,
        }
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        db['cnf']['servers'][server]['conn'].close()
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
            escaped = search_value.replace("'", "''").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions = []
            for col in column_names:
                conditions.append(f"CAST({_quote_ident(col)} AS CHAR) LIKE '%{escaped}%'")
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
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        db['cnf']['servers'][server]['conn'].close()
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

    # Get indices for the target columns
    col_names = content.get('column_names', [])
    field_indices = {field: idx for field, unit in time_fields.items() if field in col_names for idx, name in enumerate(col_names) if name == field}

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
                    row[idx] = datetime.utcfromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')
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
        db['cnf']['servers'][server]['conn'].close()
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
    result = {}
    try:
        db_connect(db, server=server, dictionary=False)

        config = get_config()
        if 'adhoc_report' in config['misc']:
            for item in config['misc']['adhoc_report']:
                logging.debug("query: {}".format(item))
                db['cnf']['servers'][server]['cur'].execute(item['sql'])

                result['rows'] = db['cnf']['servers'][server]['cur'].fetchall()
                result['title'] = item['title']
                result['sql'] = item['sql']
                result['info'] = item['info']
                result['column_names'] = [i[0] for i in db['cnf']['servers'][server]['cur'].description]
                adhoc_results.append(result.copy())
        else:
            pass

        return adhoc_results
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        db['cnf']['servers'][server]['conn'].close()
        raise ValueError(e)


def get_servers():
    proxysql_servers = []
    try:
        servers_dict = get_config()
        for server in servers_dict['servers']:
            proxysql_servers.append(server)
        return proxysql_servers
    except Exception as e:
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



def execute_change(db, server, sql):
    """
    Execute a write operation (INSERT/UPDATE/DELETE) against the specified server and return any error output.
    
    Parameters:
        db: Mutable container used to establish or hold a database connection (passed-through to connection helper).
        server (str): Name of the server from configuration to target.
        sql (str): SQL statement to execute.
    
    Returns:
        error_msg (str): Error output from the client if the command failed, or an empty string if execution succeeded.
    """
    try:
        # this is a temporary solution as using the  mysql.connector for certain writes ended up with weird results, ProxySQL
        # is not a MySQL server after all. We're investigating the issue.
        db_connect(db, server=server, dictionary=False)
        logging.debug("=" * 80)
        logging.debug("EXECUTING SQL (UPDATE/INSERT/DELETE):")
        logging.debug("=" * 80)
        logging.debug(f"Server: {server}")
        logging.debug(f"SQL: {sql}")
        logging.debug("=" * 80)
        dsn = get_config()['servers'][server]['dsn'][0]

        # Escape double quotes and backslashes in SQL to prevent shell interpretation issues
        # This allows both single and double quotes to be used in SQL statements
        escaped_sql = sql.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')

        cmd = ('mysql -h %s -P %s -u %s -p%s main   -e "%s" ' % (dsn['host'], dsn['port'], dsn['user'], dsn['passwd'], escaped_sql))
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()

        error_msg = stderr.decode().replace("mysql: [Warning] Using a password on the command line interface can be insecure.\n",'')
        if error_msg:
            logging.error(f"SQL execution error: {error_msg}")
        else:
            logging.debug("SQL executed successfully")

        return error_msg
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        logging.error(f"MySQL Connector error: {str(e)}")
        return str(e)


def extract_default_value(column_def):
    """
    Extract the DEFAULT clause value from a SQL column definition string.
    
    Parameters:
        column_def (str): A single-column definition fragment from a CREATE TABLE statement.
    
    Returns:
        The extracted default token as a string (includes surrounding quotes for quoted defaults) or `None` if no DEFAULT clause is present.
    """
    # Pattern to match DEFAULT clause
    default_pattern = r'DEFAULT\s+(?:NULL|(\'[^\']*\'|\"[^\"]*\"|\w+|\d+))'
    match = re.search(default_pattern, column_def, re.IGNORECASE)

    if match:
        return match.group(1)

    return None


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
        query = f"SHOW CREATE TABLE `{database}`.`{table_name}`"
        db['cnf']['servers'][server]['cur'].execute(query)
        create_table_result = db['cnf']['servers'][server]['cur'].fetchone()

        if not create_table_result:
            raise ValueError(f"Table '{table_name}' not found in database '{database}'")

        # The result contains two fields: Table and Create Table
        # Field names may vary, so we need to handle both possibilities
        create_table_sql = None
        if 'Create Table' in create_table_result:
            create_table_sql = create_table_result['Create Table']
        elif isinstance(create_table_result, dict):
            # Handle alternative key casing
            for key, value in create_table_result.items():
                if 'create table' in key.lower():
                    create_table_sql = value
                    break
        else:
            # Handle tuple result
            create_table_sql = create_table_result[1]

        logging.debug(f"CREATE TABLE statement:\n{create_table_sql}")

        # Extract column definitions from CREATE TABLE
        columns_info = parse_column_definitions(create_table_sql)

        # Get known constraints from ProxySQL table definitions for additional info
        proxysql_constraints = get_proxysql_table_constraints(table_name)

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

            # Merge with known ProxySQL constraints if available
            if table_name in proxysql_constraints and col_name in proxysql_constraints[table_name]:
                # Use CREATE TABLE parsing as primary, but supplement with known constraints
                for key, value in proxysql_constraints[table_name][col_name].items():
                    if key not in column_info or column_info[key] is None:
                        column_info[key] = value

            result['columns'][col_name] = column_info

        logging.debug(f"Successfully extracted schema for table '{table_name}'")

    except (mysql.connector.Error, mysql.connector.Warning) as e:
        logging.error(f"MySQL error: {e}")
        raise ValueError(f"MySQL error while extracting schema: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise ValueError(f"Unexpected error while extracting schema: {e}")
    finally:
        try:
            db['cnf']['servers'][server]['cur'].close()
        except Exception:
            pass
        try:
            db['cnf']['servers'][server]['conn'].close()
        except Exception:
            pass

    return result


def get_primary_key_columns(db, server, database, table_name):
    """
    Return the primary key column names defined for the specified table.
    
    Parses the table's CREATE TABLE SQL and extracts the PRIMARY KEY column list.
    
    Returns:
        list: Primary key column names in definition order. Returns an empty list if the table has no primary key or if the primary key cannot be determined.
    """
    try:
        db_connect(db, server=server, dictionary=True)

        # Get the CREATE TABLE statement
        query = f"SHOW CREATE TABLE `{database}`.`{table_name}`"
        db['cnf']['servers'][server]['cur'].execute(query)
        create_table_result = db['cnf']['servers'][server]['cur'].fetchone()

        if not create_table_result:
            logging.warning(f"Table '{table_name}' not found")
            return []

        # Extract CREATE TABLE SQL
        create_table_sql = None
        if 'Create Table' in create_table_result:
            create_table_sql = create_table_result['Create Table']
        else:
            create_table_sql = create_table_result[1]

        # Find PRIMARY KEY definition
        # Pattern: PRIMARY KEY (column1, column2, ...)
        pk_pattern = r'PRIMARY\s+KEY\s*\(([^)]+)\)'
        match = re.search(pk_pattern, create_table_sql, re.IGNORECASE)

        if match:
            pk_columns_str = match.group(1)
            # Split by comma and clean up column names
            pk_columns = [col.strip().strip('`"[]') for col in pk_columns_str.split(',')]
            logging.debug(f"Primary key columns for {table_name}: {pk_columns}")
            return pk_columns
        else:
            logging.warning(f"No primary key found for table {table_name}")
            return []

    except Exception as e:
        logging.error(f"Error extracting primary key for {table_name}: {e}")
        return []
    finally:
        try:
            db['cnf']['servers'][server]['cur'].close()
        except Exception:
            pass
        try:
            db['cnf']['servers'][server]['conn'].close()
        except Exception:
            pass


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
    """
    Split SQL column definitions by comma, handling nested parentheses.
    """
    columns = []
    current = ''
    paren_depth = 0

    for char in text:
        if char == '(':
            paren_depth += 1
            current += char
        elif char == ')':
            paren_depth -= 1
            current += char
        elif char == ',' and paren_depth == 0:
            columns.append(current.strip())
            current = ''
        else:
            current += char

    if current.strip():
        columns.append(current.strip())

    return columns


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


def get_proxysql_table_constraints(table_name):
    """
    Retrieve known column constraints for a ProxySQL runtime table.
    
    Parameters:
        table_name (str): Name of the ProxySQL table to look up.
    
    Returns:
        constraints (dict): A mapping where the key is the requested `table_name` and the value is a dict of column names to their constraint metadata (keys may include `type`, `nullable`, `default`, `check_constraint`, and `is_primary_key`). If the table is unknown, an empty dict is returned.
    """
    constraints = {}

    # Define known ProxySQL table constraints
    # These are based on the actual ProxySQL table structures
    proxysql_constraints = {
        'runtime_global_variables': {
            'variable_name': {
                'type': 'VARCHAR(128)',
                'nullable': False,
                'is_primary_key': True
            },
            'variable_value': {
                'type': 'VARCHAR(2048)',
                'nullable': True,
                'default': ''
            }
        },
        'runtime_mysql_servers': {
            'hostname': {
                'type': 'VARCHAR(255)',
                'nullable': False,
                'default': ''
            },
            'port': {
                'type': 'INT',
                'nullable': False,
                'default': 3306
            },
            'status': {
                'type': 'VARCHAR(50)',
                'nullable': False,
                'check_constraint': "IN ('ONLINE', 'SHUNNED', 'OFFLINE_HARD')",
                'default': 'ONLINE'
            },
            'weight': {
                'type': 'INT',
                'nullable': False,
                'default': 1
            },
            'compression': {
                'type': 'INT',
                'nullable': False,
                'default': 0
            },
            'max_connections': {
                'type': 'INT',
                'nullable': False,
                'default': 1000
            }
        },
        'runtime_mysql_users': {
            'username': {
                'type': 'VARCHAR(64)',
                'nullable': False,
                'is_primary_key': True
            },
            'password': {
                'type': 'VARCHAR(1024)',
                'nullable': True
            },
            'active': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 1
            },
            'use_ssl': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 0
            },
            'default_hostgroup': {
                'type': 'INT',
                'nullable': False,
                'default': 0
            },
            'default_schema': {
                'type': 'VARCHAR(64)',
                'nullable': True,
                'default': ''
            },
            'schema_locking': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 1
            },
            'connect_warnings': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 0
            }
        },
        'runtime_mysql_query_rules': {
            'rule_id': {
                'type': 'INT',
                'nullable': False,
                'is_primary_key': True
            },
            'active': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 1
            },
            'username': {
                'type': 'VARCHAR(64)',
                'nullable': True
            },
            'schemaname': {
                'type': 'VARCHAR(64)',
                'nullable': True
            },
            'flagIN': {
                'type': 'INT',
                'nullable': False,
                'default': 0
            },
            'match_pattern': {
                'type': 'TEXT',
                'nullable': True
            },
            'negate_match_pattern': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 0
            },
            're_modifiers': {
                'type': 'VARCHAR(255)',
                'nullable': True,
                'default': 'COLUMN_ENGINE=innodb'
            }
        },
        'runtime_mysql_replication_hostgroups': {
            'writer_hostgroup': {
                'type': 'INT',
                'nullable': False,
                'is_primary_key': True
            },
            'reader_hostgroup': {
                'type': 'INT',
                'nullable': False
            },
            'comment': {
                'type': 'VARCHAR(255)',
                'nullable': True,
                'default': ''
            }
        },
        'runtime_mysql_group_replication_hostgroups': {
            'writer_hostgroup': {
                'type': 'INT',
                'nullable': False,
                'is_primary_key': True
            },
            'backup_writer_hostgroup': {
                'type': 'INT',
                'nullable': False
            },
            'reader_hostgroup': {
                'type': 'INT',
                'nullable': False
            },
            'offline_hostgroup': {
                'type': 'INT',
                'nullable': False
            },
            'active': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 1
            },
            'max_writers': {
                'type': 'INT',
                'nullable': False,
                'default': 1
            },
            'writer_is_also_reader': {
                'type': 'INT',
                'nullable': False,
                'check_constraint': "IN (0, 1)",
                'default': 0
            },
            'max_transactions_behind': {
                'type': 'INT',
                'nullable': False,
                'default': 0
            }
        }
    }

    if table_name in proxysql_constraints:
        return {table_name: proxysql_constraints[table_name]}

    return constraints


def update_row(db, server, database, table, pk_values, column_names, data):
    """
    Update a single row in the specified table identified by primary key values.
    
    Parameters:
        db (dict): Execution context object where connection/cursor are stored (passed through to helpers).
        server (str): Server name from configuration to target for the update.
        database (str): Database/schema name containing the table.
        table (str): Table name to update.
        pk_values (dict): Mapping of primary key column names to their identifying values; values of None match IS NULL.
        column_names (Iterable[str]): Iterable of valid column names for the target table used to validate incoming columns.
        data (dict): Mapping of column names to new values; omit keys or set value to None to use NULL/DEFAULT behavior.
    
    Returns:
        dict: Result object with keys:
            - `success` (bool): `True` if the update command was issued without detected errors, `False` otherwise.
            - `error` (str|None): Error message when `success` is `False`, otherwise `None`.
    """
    result = {'success': True, 'error': None}
    try:
        if not pk_values:
            result['success'] = False
            result['error'] = 'No primary key values provided'
            return result

        # Validate column names from caller against the full column list
        allowed_columns = set(column_names)
        for col in data:
            if col not in allowed_columns:
                result['success'] = False
                result['error'] = f'Unknown column: {col!r}'
                return result

        # Determine which columns form the primary key
        pk_cols = get_primary_key_columns(db, server, database, table)
        if not pk_cols:
            pk_cols = list(pk_values.keys())  # fallback: use whatever was sent

        # Build WHERE clause from pk_values
        where_conditions = []
        for col in pk_cols:
            val = pk_values.get(col)
            if val is None:
                where_conditions.append(f"{_quote_ident(col)} IS NULL")
            else:
                escaped = str(val).replace("'", "''")
                where_conditions.append(f"{_quote_ident(col)} = '{escaped}'")

        if not where_conditions:
            result['success'] = False
            result['error'] = 'Could not build WHERE clause'
            return result

        where_clause = " WHERE " + " AND ".join(where_conditions)

        # Build SET clause
        set_clauses = []
        for column, value in data.items():
            if value is None:
                set_clauses.append(f"{_quote_ident(column)} = NULL")
            else:
                escaped_value = str(value).replace("'", "''")
                set_clauses.append(f"{_quote_ident(column)} = '{escaped_value}'")

        if not set_clauses:
            result['success'] = False
            result['error'] = 'No columns to update'
            return result

        sql = "UPDATE {}.{} SET {}{}".format(
            _quote_ident(database), _quote_ident(table), ', '.join(set_clauses), where_clause
        )

        logging.debug("Update SQL: {}".format(sql))
        error = execute_change(db, server, sql)

        if "ERROR" in error:
            result['success'] = False
            result['error'] = error

    except Exception as e:
        result['success'] = False
        result['error'] = str(e)

    return result


def delete_row(db, server, database, table, pk_values):
    """
    Delete a row identified by primary key values from the specified table.
    
    If the table's primary key columns can be discovered, those columns are used; otherwise the keys of `pk_values` are used as a fallback. NULL values in `pk_values` are translated to `IS NULL` in the WHERE clause. On failure the function returns an error message and does not raise.
    
    Parameters:
        pk_values (dict): Mapping of primary key column names to their values; use `None` to match SQL NULL.
    
    Returns:
        dict: A result object with keys:
            - `success` (bool): `True` if the delete was issued without detected error, `False` otherwise.
            - `error` (str or None): Error message when `success` is `False`, otherwise `None`.
    """
    result = {'success': True, 'error': None}
    try:
        if not pk_values:
            result['success'] = False
            result['error'] = 'No primary key values provided'
            return result

        # Determine which columns form the primary key
        pk_cols = get_primary_key_columns(db, server, database, table)
        if not pk_cols:
            pk_cols = list(pk_values.keys())  # fallback: use whatever was sent

        # Build WHERE clause from pk_values
        where_conditions = []
        for col in pk_cols:
            val = pk_values.get(col)
            if val is None:
                where_conditions.append(f"{_quote_ident(col)} IS NULL")
            else:
                escaped = str(val).replace("'", "''")
                where_conditions.append(f"{_quote_ident(col)} = '{escaped}'")

        if not where_conditions:
            result['success'] = False
            result['error'] = 'Could not build WHERE clause'
            return result

        where_clause = " AND ".join(where_conditions)
        sql = f"DELETE FROM {_quote_ident(database)}.{_quote_ident(table)} WHERE {where_clause}"

        logging.debug(f"Delete SQL: {sql}")
        error = execute_change(db, server, sql)

        if "ERROR" in error:
            result['success'] = False
            result['error'] = error

    except Exception as e:
        result['success'] = False
        result['error'] = str(e)
        logging.error(f"Error in delete_row: {e}")

    return result


def insert_row(db, server, database, table, column_names, data):
    """
    Insert a new row into a table, using provided column names and values while validating against the live table schema.
    
    Parameters:
        db: Execution context / connection holder used by helper functions (not a raw connection).
        server (str): Server name from configuration to run the insert against.
        database (str): Database/schema name containing the target table.
        table (str): Target table name.
        column_names (list[str]): List of column names the caller intends to set; entries named `'variable_name'` are ignored.
        data (dict): Mapping of column name to value for the insert; missing keys or keys with value `None` are omitted so the column's DEFAULT is used.
    
    Returns:
        dict: A result mapping with keys:
            - 'success' (bool): `True` if the INSERT was issued without detected errors, `False` otherwise.
            - 'error' (str|None): Error message on failure, or `None` on success.
    """
    result = {'success': True, 'error': None}
    try:
        # Fetch live schema to build identifier whitelist
        content = get_table_content(db, server, database, table)
        allowed_columns = set(content['column_names'])

        # Validate caller-supplied column names against the live schema
        for col in column_names:
            if col != 'variable_name' and col not in allowed_columns:
                result['success'] = False
                result['error'] = f'Unknown column: {col!r}'
                return result

        # Exclude variable_name from insert if it exists
        columns = [col for col in column_names if col != 'variable_name']
        values = []
        insert_columns = []

        for column in columns:
            if column in data and data[column] is not None:
                value = data[column]
                insert_columns.append(column)
                # Handle NULL values
                if value is None:
                    values.append("NULL")
                else:
                    escaped_value = str(value).replace("'", "''")
                    values.append("'{}'".format(escaped_value))
            # else: column not in data OR value is None, skip it (will use DEFAULT)

        if not insert_columns:
            result['success'] = False
            result['error'] = 'No columns to insert'
            return result

        sql = "INSERT INTO {}.{} ({}) VALUES ({})".format(
            _quote_ident(database), _quote_ident(table),
            ', '.join(_quote_ident(c) for c in insert_columns),
            ', '.join(values)
        )

        logging.debug("Insert SQL: {}".format(sql))
        error = execute_change(db, server, sql)

        if "ERROR" in error:
            result['success'] = False
            result['error'] = error

    except Exception as e:
        result['success'] = False
        result['error'] = str(e)

    return result


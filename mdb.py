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
import yaml
import subprocess
import sqlite3
import re
import json
from datetime import datetime

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

sql_get_databases = "show databases"
sql_show_table_content = "select * from %s.%s order by 1;"
sql_show_tables = "show tables from %s;"

def get_config(config="config/config.yml"):
    logging.debug("Using file: %s" % (config))
    try:
        with open(config, 'r') as yml:
            cfg = yaml.safe_load(yml)
        return cfg
    except Exception as e:
        raise ValueError("Error opening or parsing the file: %" % config)


def dict_to_yaml(data, indent=0, prev_key=None):
    """
    Convert a dictionary to YAML format with proper indentation and improved readability.
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

            if isinstance(value, (dict, list)) and value:
                yaml_str += f"{indent_str}{key}:\n"
                yaml_str += dict_to_yaml(value, indent + 1, key)
            else:
                yaml_str += f"{indent_str}{key}: {format_yaml_value(value)}\n"
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yaml_str += f"{indent_str}- "
                yaml_str += dict_to_yaml_inline(item, indent)
            else:
                yaml_str += f"{indent_str}- {format_yaml_value(item)}\n"
    else:
        yaml_str = format_yaml_value(data)

    return yaml_str


def dict_to_yaml_inline(data, indent=0):
    """
    Convert a dictionary to inline YAML format for arrays.
    """
    items = []
    for key, value in data.items():
        items.append(f'"{key}": {format_yaml_value(value)}')

    return "{" + ", ".join(items) + "}\n"


def format_yaml_value(value):
    """Format a value for YAML output."""
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
    """Validate YAML syntax."""
    try:
        yaml.safe_load(yaml_content)
        return True
    except Exception as e:
        raise ValueError(f"Invalid YAML syntax: {str(e)}")


def form_data_to_yaml(form_data):
    """
    Convert form data to YAML configuration format.
    This function reconstructs the config.yml structure from the form data.
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

    # Remove empty sections
    config = {k: v for k, v in config.items() if v}

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


def db_connect(db, server, autocommit=False, buffered=False, dictionary=True):
    try:
        db['cnf'] = get_config()

        config = db['cnf']['servers'][server]['dsn'][0]
        logging.debug(db['cnf']['servers'][server]['dsn'][0])
        db['cnf']['servers'][server]['conn'] = mysql.connector.connect(**config,raise_on_warnings=True, get_warnings=True, connection_timeout=3, )

        if  db['cnf']['servers'][server]['conn'].is_connected():
            logging.debug("Connected successfully to %s as %s db=%s" % (
                config['host'],
                config['user'],
                config['db']))

        db['cnf']['servers'][server]['conn'] .autocommit = autocommit
        db['cnf']['servers'][server]['conn'] .get_warnings = True

        db['cnf']['servers'][server]['cur'] = db['cnf']['servers'][server]['conn'].cursor(buffered=buffered,
                                                                                            dictionary=dictionary)
        logging.debug("buffered: %s, dictionary: %s, autocommit: %s" % (buffered, dictionary, autocommit))

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

            db['cnf']['servers'][server]['cur'].execute(sql_show_tables % i['name'])
            for table in db['cnf']['servers'][server]['cur'].fetchall():
                # hide tables as per global or per server config
                # Supports both exact string matches and regex patterns
                if not should_hide_table(table['tables'], table_exception_list):
                    all_dbs[server][i['name']].append(table['tables'])
        db['cnf']['servers'][server]['cur'].close()
        return all_dbs
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        raise ValueError(e)


def get_config_diff():
    """
    Get configuration differences across Disk, Memory, and Runtime layers.
    Returns a dictionary with summary and detailed diff information.
    """
    try:
        # Get config to access hide_tables and skip_variables
        config = get_config()
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
        db_connect(query_db, server='proxysql', dictionary=False)
        query_db['cnf']['servers']['proxysql']['conn'].database = 'main'

        # Get all tables from main database
        query_db['cnf']['servers']['proxysql']['cur'].execute("SHOW TABLES")
        all_tables = [table[0] for table in query_db['cnf']['servers']['proxysql']['cur'].fetchall()]

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
            if any(table.startswith(prefix) for prefix in ['mysql_', 'admin_', 'global_', 'scheduler', 'restapi']):
                tables_to_diff.append(table)

        query_db['cnf']['servers']['proxysql']['conn'].close()

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
                    db_connect(query_db, server='proxysql', dictionary=False)

                    # Query and get results
                    query_db['cnf']['servers']['proxysql']['cur'].execute(query)
                    rows = query_db['cnf']['servers']['proxysql']['cur'].fetchall()

                    # Convert rows to dictionaries for comparison
                    if rows:
                        column_names = [desc[0] for desc in query_db['cnf']['servers']['proxysql']['cur'].description]
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

                    query_db['cnf']['servers']['proxysql']['conn'].close()

                except Exception as e:
                    # Table might not exist in all layers
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
    '''returns with a dict with two keys "column_names" = list and  rows = tuples '''
    content = {}
    try:
        logging.debug("server: {} - db: {} - table:{}".format(server, database, table))
        db_connect(db, server=server, dictionary=False)
        data = (database, table)

        string = (sql_show_table_content % data)
        logging.debug("query: {}".format(string))

        db['cnf']['servers'][server]['cur'].execute(string)

        content['rows'] = db['cnf']['servers'][server]['cur'].fetchall()
        content['column_names'] = [i[0] for i in db['cnf']['servers'][server]['cur'].description]
        content['misc'] = get_config()['misc']
        return content
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
    '''returns with a dict with two keys "column_names" = list and  rows = tuples '''
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
        db['cnf']['servers'][server]['conn'].close
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

def get_read_only(server):
    try:
        config = get_config()
        if 'read_only' not in config['servers'][server]:
            read_only = config['global']['read_only']
        else:
            read_only = config['servers'][server]['read_only']
        return read_only
    except:
        raise ValueError("Cannot get read_only status from the config file")



def execute_change(db, server, sql):
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
        return e


def extract_default_value(column_def):
    """
    Extract DEFAULT value from a column definition.
    Returns the default value or None if not found.
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
        elif 'Create Table' in create_table_result:
            create_table_sql = create_table_result['Create Table']
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
        db['cnf']['servers'][server]['conn'].close()
        logging.error(f"MySQL error: {e}")
        raise ValueError(f"MySQL error while extracting schema: {e}")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise ValueError(f"Unexpected error while extracting schema: {e}")

    return result


def get_primary_key_columns(db, server, database, table_name):
    """
    Extract primary key column names from a table's CREATE TABLE statement.

    Args:
        db: Database connection dict
        server: Server name from config
        database: Database name
        table_name: Name of the table

    Returns:
        list: List of column names that form the primary key
              Returns empty list if no primary key found

    Example:
        For PRIMARY KEY (hostgroup_id, hostname, port)
        Returns: ['hostgroup_id', 'hostname', 'port']
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
        import re
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


def parse_column_definitions(create_table_sql):
    """
    Parse CREATE TABLE SQL to extract column definitions.
    Returns dict of column info with type, nullable, default, and CHECK constraints.
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
    Parse a single column definition from CREATE TABLE.
    Returns dict with column information.
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
    Get known constraints for ProxySQL tables.
    Based on ProxySQL documentation: https://proxysql.com/documentation/main-runtime/

    Returns a dict of table constraints including CHECK constraints and defaults.
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


def update_row(db, server, database, table, row_index, column_names, data):
    """
    Update a specific row in a table using ALL primary key columns.
    Handles both single and composite primary keys.
    Returns dict with 'success' and 'error' keys.
    """
    result = {'success': True, 'error': None}
    try:
        # Get the current row data first
        content = get_table_content(db, server, database, table)
        if row_index >= len(content['rows']):
            result['success'] = False
            result['error'] = 'Row not found'
            return result

        row = content['rows'][row_index]

        # Build WHERE clause - using variable_name as identifier if available (legacy behavior)
        where_clause = ""
        if 'variable_name' in data and 'variable_name' in content['column_names']:
            # Special handling for tables with variable_name column
            where_clause = " WHERE variable_name = '{}'".format(data['variable_name'])
        else:
            # Get ALL primary key columns for this table
            pk_columns = get_primary_key_columns(db, server, database, table)

            if not pk_columns:
                # Fallback to first column if no primary key found
                logging.warning(f"No primary key found for {table}, using first column")
                pk_columns = [column_names[0]] if column_names else ['id']

            # Build WHERE clause using ALL primary key columns
            where_conditions = []
            for pk_col in pk_columns:
                try:
                    # Find the index of this pk column in the column_names list
                    col_index = content['column_names'].index(pk_col)
                    pk_value = row[col_index]

                    # Handle NULL values
                    if pk_value is None:
                        where_conditions.append(f"{pk_col} IS NULL")
                    else:
                        # Escape single quotes in values
                        escaped_value = str(pk_value).replace("'", "''")
                        where_conditions.append(f"{pk_col} = '{escaped_value}'")
                except (ValueError, IndexError) as e:
                    logging.error(f"Error processing primary key column {pk_col}: {e}")
                    result['success'] = False
                    result['error'] = f'Primary key column {pk_col} not found in table data'
                    return result

            where_clause = " WHERE " + " AND ".join(where_conditions)

        # Build SET clause - exclude variable_name from updates if it's a primary key
        set_clauses = []
        for column, value in data.items():
            if column != 'variable_name' or 'variable_name' not in where_clause:
                # Handle NULL values
                if value is None:
                    set_clauses.append("{} = NULL".format(column))
                else:
                    # Escape single quotes in values
                    escaped_value = str(value).replace("'", "''")
                    set_clauses.append("{} = '{}'".format(column, escaped_value))

        if not set_clauses:
            result['success'] = False
            result['error'] = 'No columns to update'
            return result

        sql = "UPDATE {}.{} SET {}{}".format(
            database, table, ', '.join(set_clauses), where_clause
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


def delete_row(db, server, database, table, row_index):
    """
    Delete a specific row from a table using ALL primary key columns.
    Handles both single and composite primary keys.
    Returns dict with 'success' and 'error' keys.
    """
    result = {'success': True, 'error': None}
    try:
        # Get table content
        content = get_table_content(db, server, database, table)
        if row_index >= len(content['rows']):
            result['success'] = False
            result['error'] = 'Row not found'
            return result

        row = content['rows'][row_index]

        # Get ALL primary key columns for this table
        pk_columns = get_primary_key_columns(db, server, database, table)

        if not pk_columns:
            # Fallback to first column if no primary key found
            logging.warning(f"No primary key found for {table}, using first column")
            pk_columns = [content['column_names'][0]]

        # Build WHERE clause using ALL primary key columns
        where_conditions = []
        for pk_col in pk_columns:
            try:
                # Find the index of this pk column in the column_names list
                col_index = content['column_names'].index(pk_col)
                pk_value = row[col_index]

                # Handle NULL values
                if pk_value is None:
                    where_conditions.append(f"{pk_col} IS NULL")
                else:
                    # Escape single quotes in values
                    escaped_value = str(pk_value).replace("'", "''")
                    where_conditions.append(f"{pk_col} = '{escaped_value}'")
            except (ValueError, IndexError) as e:
                logging.error(f"Error processing primary key column {pk_col}: {e}")
                result['success'] = False
                result['error'] = f'Primary key column {pk_col} not found in table data'
                return result

        where_clause = " AND ".join(where_conditions)
        sql = f"DELETE FROM {database}.{table} WHERE {where_clause}"

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
    Insert a new row into a table.
    Returns dict with 'success' and 'error' keys.
    """
    result = {'success': True, 'error': None}
    try:
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
                    # Escape single quotes in values
                    escaped_value = str(value).replace("'", "''")
                    values.append("'{}'".format(escaped_value))
            # else: column not in data OR value is None, skip it (will use DEFAULT)

        if not insert_columns:
            result['success'] = False
            result['error'] = 'No columns to insert'
            return result

        sql = "INSERT INTO {}.{} ({}) VALUES ({})".format(
            database, table, ', '.join(insert_columns), ', '.join(values)
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


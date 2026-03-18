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

import logging
import secrets
import subprocess
from collections import defaultdict
from flask import Flask, render_template, request, session, url_for, flash, redirect, jsonify, abort
from functools import wraps
import re
import errno
import os
import tempfile
import yaml
import mdb

app = Flask(__name__)

try:
    _git_commit = subprocess.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'],
        stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(os.path.abspath(__file__))
    ).decode().strip()
except Exception:
    _git_commit = ''

@app.context_processor
def inject_git_commit():
    return {'git_commit': _git_commit}

config = "config/config.yml"


def _atomic_write(path, content):
    """
    Write the given content to a file at `path` atomically.
    
    This creates a temporary file in the same directory as `path`, writes `content` to it, flushes and fsyncs the data, then atomically replaces the target file with the temp file. If an error occurs during write or replace, the temporary file is removed (best-effort) and the original exception is re-raised.
    
    Parameters:
        path (str): Filesystem path to write to. The final file at this path will be created or replaced.
        content (str): Data to write to the file.
    
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
        os.replace(tmp_path, path)
    except OSError as exc:
        logging.warning(f"_atomic_write: os.replace failed errno={exc.errno} ({exc}); path={path}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if exc.errno in (errno.EXDEV, errno.EBUSY):
            # Docker file bind-mount: rename() can fail with EXDEV (cross-device)
            # or EBUSY (device busy) when the target is a bind-mounted file.
            # Fall back to a direct overwrite which is safe enough in this context.
            logging.warning(f"_atomic_write: rename errno={exc.errno} fallback — writing directly to {path}")
            with open(path, 'w') as f:
                f.write(content)
        else:
            raise


db = defaultdict(lambda: defaultdict(dict))

# read/apply the flask config from the config file
flask_custom_config = mdb.get_config(config)
for key in flask_custom_config['flask']:
    app.config[key] = flask_custom_config['flask'][key]
# YAML parses an all-digit SECRET_KEY as int; Flask requires str/bytes.
if not isinstance(app.config.get('SECRET_KEY'), (str, bytes)):
    app.config['SECRET_KEY'] = str(app.config['SECRET_KEY'])


mdb.logging.debug(flask_custom_config)

def login_required(f):
    """
    Decorator that requires a user to be logged in before calling the wrapped view.
    
    If the session key 'logged_in' is not present or falsy, flashes a warning message and redirects the client to the login page; otherwise invokes the original view function.
    
    Parameters:
        f (callable): The view function to wrap.
    
    Returns:
        callable: A wrapped view function that enforces the login requirement.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('You must be logged in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def ensure_csrf_token():
    """
    Ensure a CSRF token exists in the user's session.
    
    If no token is present, generates a 32-byte random token (returned as a 64-character hexadecimal string)
    and stores it in session['csrf_token'].
    """
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)


@app.before_request
def csrf_protect():
    """
    Validate CSRF token for incoming POST requests (excluding the login endpoint) and abort with HTTP 403 on missing or invalid tokens.
    
    When the request method is POST and the endpoint is not 'login', this checks the `_csrf_token` form field or the `X-CSRF-Token` header against the `csrf_token` stored in the session; if the token is missing or does not match, the request is aborted with a 403 response.
    """
    if request.method == 'POST' and request.endpoint != 'login':
        token = (request.form.get('_csrf_token')
                 or request.headers.get('X-CSRF-Token'))
        if not token or token != session.get('csrf_token'):
            abort(403)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Render the login page and authenticate the admin user.
    
    On POST, validates submitted credentials against the configured admin username and password; if they match, sets session['logged_in'] and redirects to the database list view. On GET or failed authentication, renders the login page and includes an error message when credentials are invalid.
    Returns:
    	A Flask response: a redirect to the database list on successful authentication, or the rendered login page (login.html) with an optional error message.
    """
    session.clear()
    message=""
    auth_cfg = mdb.get_config(config)['auth']
    admin_user = auth_cfg['admin_user']
    admin_password = auth_cfg['admin_password']
    readonly_user = auth_cfg.get('readonly_user', 'readonly')
    readonly_password = auth_cfg.get('readonly_password', '')

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if admin_user == username and admin_password == password:
            session['logged_in'] = True
            session['role'] = 'admin'
            return redirect(url_for('render_list_dbs'))
        elif readonly_user and readonly_password and username == readonly_user and password == readonly_password:
            session['logged_in'] = True
            session['role'] = 'readonly'
            return redirect(url_for('render_list_dbs'))
        message="Invalid credentials!"

    show_default_creds = (
        (admin_user == 'admin' and admin_password == 'admin42')
        or (readonly_user == 'readonly' and readonly_password == 'readonly42')
    )
    return render_template("login.html", message=message, show_default_creds=show_default_creds)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def render_list_dbs():
    try:
        server = mdb.get_default_server()
    except ValueError:
        if session.get('role') == 'readonly':
            return render_template("error.html", error="No servers configured. Ask an admin to set one up."), 503
        flash('No servers configured. Please add one below.', 'warning')
        return redirect(url_for('render_settings', action='edit'))
    try:
        session['server'] = server
        session['dblist'] = mdb.get_all_dbs_and_tables(db, server)
        session['servers'] = mdb.get_servers()
        session['read_only'] = mdb.get_read_only(server)
        if session.get('role') == 'readonly':
            session['read_only'] = True
        session['misc'] = mdb.get_config(config)['misc']
        recent = mdb.load_query_history(server, limit=10)
        session['history'] = [e['sql'] for e in recent]

        return render_template("list_dbs.html", server=server)
    except Exception as e:
        raise ValueError(e)

@app.route('/<server>/')
@app.route('/<server>/<database>/<table>/')
@login_required
def render_show_table_content(server, database="main", table="global_variables"):
    try:
        # refresh the tablelist if changing to a new server

        if server not in session['dblist']:
            session['dblist'].update(mdb.get_all_dbs_and_tables(db, server))

        session['servers'] = mdb.get_servers()
        session['server'] = server
        recent = mdb.load_query_history(server, limit=10)
        session['history'] = [e['sql'] for e in recent]
        session['table'] = table
        session['database'] = database
        session['misc'] = mdb.get_config(config)['misc']
        session['read_only'] = mdb.get_read_only(server)
        if session.get('role') == 'readonly':
            session['read_only'] = True
        content = mdb.get_table_content(db, server, database, table)
        mdb.process_table_content(table,content)
        return render_template("show_table_info.html", content=content)
    except Exception as e:
        raise ValueError(e)

@app.route('/<server>/<database>/<table>/sql/', methods=['GET', 'POST'])
@login_required
def render_change(server, database, table):
    """
    Handle ad-hoc SQL submitted from the table view: execute SELECT queries or data-changing statements, update request history, and render the table view with results.
    
    Executes the SQL from the submitted form. If the statement is a SELECT, runs it as an adhoc query and returns its result; otherwise executes the change and refreshes the table content. Updates session history with non-duplicate successful statements and sets an error or success message for rendering.
    
    Returns:
        Rendered HTML for "show_table_info.html" containing `content`, `error`, and `message`.
    
    Raises:
        ValueError: If any unexpected exception occurs during processing.
    """
    try:
        error = ""
        message = ""
        ret = ""
        session['sql'] = request.form["sql"]


        mdb.logging.debug(session['history'])
        select = re.search(r'^\s*SELECT\b.*\bFROM\b', session['sql'], re.I | re.S)
        if not select and session.get('role') == 'readonly':
            error = "Read-only user cannot execute non-SELECT statements"
            content = mdb.get_table_content(db, server, database, table)
            return render_template("show_table_info.html", content=content, error=error, message="")
        if select:
            content = mdb.execute_adhoc_query(db, server, session['sql'])
            content['order'] = 'true'
        else:
            ret = mdb.execute_change(db, server, session['sql'])
            content = mdb.get_table_content(db, server, database, table)

        if "ERROR" in ret:
            error = ret
        else:
            message = "Success"
        if session['sql'].replace("\r\n","") not in session['history'] and not error:
            session['history'].append(session['sql'].replace("\r\n",""))
            mdb.append_query_history(server, session['sql'].replace("\r\n",""), session.get('role', 'admin'))

        return render_template("show_table_info.html", content=content, error=error, message=message)
    except Exception as e:
        raise ValueError(e)

@app.route('/<server>/adhoc/')
@login_required
def adhoc_report(server):
    try:

        adhoc_results = mdb.execute_adhoc_report(db, server)
        return render_template("show_adhoc_report.html", adhoc_results=adhoc_results)
    except Exception as e:
        raise ValueError(e)


@app.route('/settings/<action>/', methods=['GET', 'POST'])
@login_required
def render_settings(action):
    """
    Render the settings page and optionally persist updated configuration.

    Parameters:
        action (str): 'edit' to load the current configuration file for editing, or 'save' to validate and atomically persist the submitted YAML configuration.

    Returns:
        A Flask response rendering the 'settings.html' template with `config_file_content` and `message`.

    Raises:
        ValueError: If YAML validation, file I/O, or write operations fail.
    """
    if session.get('role') == 'readonly':
        abort(403)
    try:
        config_file_content = ""
        message = ""
        if action == 'edit':
            with open(config, "r") as f:
                config_file_content = f.read()
        if action == 'save':
            raw = request.form["settings"]
            mdb.validate_yaml(raw)
            mdb.validate_config_shape(yaml.safe_load(raw))

            # back it up first
            with open(config, "r") as src, open(config + ".bak", "w") as dest:
                dest.write(src.read())

            _atomic_write(config, raw)
            message = "success"
        return render_template("settings.html", config_file_content=config_file_content, message=message)
    except Exception as e:
        logging.exception(f"Error in settings/{action}/")
        raise ValueError(e)


@app.route('/settings/ui_save/', methods=['POST'])
@login_required
def settings_ui_save():
    """
    Save UI-submitted settings to the application's configuration after validation and backup.

    Validates the provided YAML and configuration shape, writes a backup of the current config file, and atomically replaces the config with the validated content.

    Returns:
        dict: On success, JSON {'success': True, 'message': 'Settings saved successfully'}.
        dict: On validation failure, JSON {'success': False, 'error': <message>} with HTTP 400.
        dict: On other errors, JSON {'success': False, 'error': <message>}.
    """
    if session.get('role') == 'readonly':
        abort(403)
    try:
        # Get form data and build YAML
        form_data = request.form.to_dict()
        yaml_config = mdb.form_data_to_yaml(form_data)

        # Validate before touching the existing config
        try:
            mdb.validate_yaml(yaml_config)
            mdb.validate_config_shape(yaml.safe_load(yaml_config))
        except Exception as ve:
            logging.error(f"settings_ui_save validation failed: {ve}")
            return jsonify({'success': False, 'error': str(ve)}), 400

        # Back up current config, then write
        with open(config, "r") as src, open(config + ".bak", "w") as dest:
            dest.write(src.read())
        _atomic_write(config, yaml_config)

        return jsonify({'success': True, 'message': 'Settings saved successfully'})
    except Exception as e:
        logging.exception(f"Error saving settings from UI: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/settings/load_ui/', methods=['GET'])
@login_required
def settings_load_ui():
    """
    Return the current application configuration formatted for the UI.

    Returns:
        Response: JSON object with either:
            - {'success': True, 'config': <dict>} on success, or
            - {'success': False, 'error': '<error message>'} on failure.
    """
    if session.get('role') == 'readonly':
        abort(403)
    try:
        config_data = mdb.get_config(config)
        return jsonify({'success': True, 'config': config_data})
    except Exception as e:
        logging.exception(f"Error loading config for UI: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/settings/export/', methods=['GET'])
@login_required
def settings_export():
    """
    Provide the YAML representation of the current configuration.

    Returns:
        dict: JSON-serializable payload with either:
            - {'success': True, 'yaml': <str>} on success, where <str> is the config YAML, or
            - {'success': False, 'error': <str>} on failure, where <str> is the error message.
    """
    if session.get('role') == 'readonly':
        abort(403)
    try:
        config_data = mdb.get_config(config)
        yaml_content = mdb.dict_to_yaml(config_data)
        return jsonify({'success': True, 'yaml': yaml_content})
    except Exception as e:
        logging.exception(f"Error exporting config: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/settings/import/', methods=['POST'])
@login_required
def settings_import():
    """
    Import a new application configuration from uploaded YAML content.

    Validates the uploaded YAML for syntax and required configuration shape before modifying persistent state, creates a backup of the existing config file as "<config>.bak", then atomically replaces the configuration file with the provided YAML. On success returns a JSON object indicating success; on failure returns a JSON object with an error message and logs the exception.

    Returns:
        dict: JSON-serializable mapping. On success: `{'success': True, 'message': 'Configuration imported successfully'}`.
              On error: `{'success': False, 'error': '<error message>'}`.
    """
    if session.get('role') == 'readonly':
        abort(403)
    try:
        # Get uploaded YAML content
        yaml_content = request.form.get('yaml_content', '')

        # Validate YAML syntax and required shape before touching the existing config
        mdb.validate_yaml(yaml_content)
        mdb.validate_config_shape(yaml.safe_load(yaml_content))

        # Back up current config
        with open(config, "r") as src, open(config + ".bak", "w") as dest:
            dest.write(src.read())

        # Write new config
        _atomic_write(config, yaml_content)

        return jsonify({'success': True, 'message': 'Configuration imported successfully'})
    except Exception as e:
        logging.exception(f"Error importing config: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/<server>/config_diff/', methods=['GET', 'POST'])
@login_required
def render_config_diff(server):
    """
    Render the configuration-diff page for the specified server.
    
    Parameters:
        server (str): Identifier of the server whose configuration diff will be displayed.
    
    Returns:
        flask.Response: Rendered HTML response for the configuration diff page.
    """
    return render_template('config_diff.html', server=server)


@app.route('/<server>/config_diff/get', methods=['POST'])
@login_required
def get_config_diff(server):
    """
    Return configuration differences for the given ProxySQL server.
    
    Parameters:
        server (str): Identifier of the server whose Disk/Memory/Runtime configuration differences should be retrieved.
    
    Returns:
        Response: JSON object with `success` (bool) and:
            - `diff` (object) when `success` is True: the configuration differences grouped by source.
            - `error` (str) when `success` is False: error message explaining the failure.
    """
    try:
        diff_data = mdb.get_config_diff(server)
        return jsonify({'success': True, 'diff': diff_data})
    except Exception as e:
        logging.exception(f"Error getting config diff: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/update_config_skip_variables', methods=['POST'])
@login_required
def update_config_skip_variables():
    """
    Update the list of variables to skip when computing configuration differences.

    Expects a JSON request body containing a `skip_variables` array of variable names to store
    under the config's `global.config_diff_skip_variable` key. Persists the updated configuration
    to disk (creates a `.bak` backup of the existing config before writing).

    Returns:
        dict: JSON response with `{'success': True}` on success, or
              `{'success': False, 'error': <message>}` on failure describing the error.
    """
    if session.get('role') == 'readonly':
        abort(403)
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        skip_variables = data.get('skip_variables', [])

        # Load current config
        config_data = mdb.get_config(config)

        # Update skip_variables in global section
        if 'global' not in config_data:
            config_data['global'] = {}
        config_data['global']['config_diff_skip_variable'] = skip_variables

        # Back up current config, then write
        with open(config, "r") as src, open(config + ".bak", "w") as dest:
            dest.write(src.read())
        _atomic_write(config, mdb.dict_to_yaml(config_data))

        logging.info(f"Updated config_diff_skip_variable: {skip_variables}")
        return jsonify({'success': True})
    except Exception as e:
        logging.exception(f"Error updating config skip variables: {e}")
        return jsonify({'success': False, 'error': str(e)})


# API Routes for Inline Editing
@app.route('/api/update_row', methods=['POST'])
@login_required
def api_update_row():
    """
    Handle an API request to update a single row in a table.
    
    Expects a JSON payload with the following keys in the request body:
    - server (str): target server identifier.
    - database (str): target database name.
    - table (str): target table name.
    - pkValues (list): primary key values identifying the row to update.
    - columnNames (list): column names corresponding to the data values.
    - data (list): values to write for the specified columns.
    
    Behavior:
    - Rejects requests with missing/invalid JSON with a 400 response.
    - Rejects updates if the server/table is read-only or the table name starts with "runtime_" with a 403 response.
    - Delegates the update to mdb.update_row and returns its result as JSON.
    - On unexpected errors returns a JSON object with `{'success': False, 'error': <message>}`.
    
    Returns:
    A JSON response produced by the update operation or an error object; HTTP status codes:
    - 200 for successful update responses from mdb.update_row,
    - 400 for invalid requests,
    - 403 for attempts to modify read-only/runtime tables.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        server = data['server']
        database = data['database']
        table = data['table']
        pk_values = data['pkValues']
        column_names = data['columnNames']
        row_data = data['data']

        if mdb.get_read_only(server) or table.startswith('runtime_') or session.get('role') == 'readonly':
            return jsonify({'success': False, 'error': 'table is read-only'}), 403

        logging.debug("=" * 80)
        logging.debug("API REQUEST: /api/update_row")
        logging.debug("=" * 80)
        logging.debug(f"Server: {server}")
        logging.debug(f"Database: {database}")
        logging.debug(f"Table: {table}")
        logging.debug(f"PK Values: {pk_values}")
        logging.debug(f"Column Names: {column_names}")
        logging.debug(f"Row Data: {row_data}")
        logging.debug("=" * 80)

        result = mdb.update_row(db, server, database, table, pk_values, column_names, row_data)
        logging.debug(f"Update result: {result}")
        return jsonify(result)
    except Exception as e:
        logging.exception(f"API error in update_row: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/delete_row', methods=['POST'])
@login_required
def api_delete_row():
    """
    Delete a single row from the specified table on the given server.
    
    Expects a JSON payload with the following keys in the request body:
        server (str): Server identifier to operate on.
        database (str): Database/schema name containing the table.
        table (str): Table name from which to delete the row.
        pkValues (dict): Mapping of primary key column names to their values identifying the row.
    
    Behavior:
        - Rejects the request with HTTP 400 if the JSON payload is missing or invalid.
        - Rejects the request with HTTP 403 if the target server/table is read-only or if the table name starts with "runtime_".
        - Calls the underlying data layer to perform the delete; on success returns that result as JSON.
        - On unexpected errors returns JSON with `{'success': False, 'error': <message>}`.
    
    Returns:
        JSON object with the operation result (e.g., `{'success': True, ...}` on success or `{'success': False, 'error': <message>}` on failure).
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        server = data['server']
        database = data['database']
        table = data['table']
        pk_values = data['pkValues']

        if mdb.get_read_only(server) or table.startswith('runtime_') or session.get('role') == 'readonly':
            return jsonify({'success': False, 'error': 'table is read-only'}), 403

        logging.debug("=" * 80)
        logging.debug("API REQUEST: /api/delete_row")
        logging.debug("=" * 80)
        logging.debug(f"Server: {server}")
        logging.debug(f"Database: {database}")
        logging.debug(f"Table: {table}")
        logging.debug(f"PK Values: {pk_values}")
        logging.debug("=" * 80)

        result = mdb.delete_row(db, server, database, table, pk_values)
        logging.debug(f"Delete result: {result}")
        return jsonify(result)
    except Exception as e:
        logging.exception(f"API error in delete_row: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/insert_row', methods=['POST'])
@login_required
def api_insert_row():
    """
    Insert a new row into the specified table on the given server.
    
    Expects a JSON request body with these keys:
    - server (str): target server identifier.
    - database (str): database/schema name.
    - table (str): target table name.
    - columnNames (list[str]): list of column names for the new row.
    - data (list|dict): values for the new row (format expected by the underlying DB helper).
    
    If the target server or table is read-only or the table name starts with "runtime_", the request is rejected with a 403 response. If the request body is missing or invalid, a 400 response is returned. On success, returns the JSON result produced by the underlying insert operation; on error returns JSON with {'success': False, 'error': <message>}.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        server = data['server']
        database = data['database']
        table = data['table']
        column_names = data['columnNames']
        row_data = data['data']

        if mdb.get_read_only(server) or table.startswith('runtime_') or session.get('role') == 'readonly':
            return jsonify({'success': False, 'error': 'table is read-only'}), 403

        logging.debug("=" * 80)
        logging.debug("API REQUEST: /api/insert_row")
        logging.debug("=" * 80)
        logging.debug(f"Server: {server}")
        logging.debug(f"Database: {database}")
        logging.debug(f"Table: {table}")
        logging.debug(f"Column Names: {column_names}")
        logging.debug(f"Row Data: {row_data}")
        logging.debug("=" * 80)

        result = mdb.insert_row(db, server, database, table, column_names, row_data)
        logging.debug(f"Insert result: {result}")
        return jsonify(result)
    except Exception as e:
        logging.exception(f"API error in insert_row: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/get_schema', methods=['GET'])
@login_required
def api_get_schema():
    """
    Retrieve schema information for a table, including CHECK constraints and default values.
    
    Parameters:
        table (str, query param): Name of the table to inspect; required.
    
    Returns:
        dict: JSON object with `success` (bool). On success includes `schema` containing the table schema details; on failure includes `error` with an error message.
    """
    try:
        table_name = request.args.get('table', '')
        if not table_name:
            return jsonify({'success': False, 'error': 'Table name required'})

        # Get server from session
        server = session.get('server', 'default')
        database = session.get('database', 'main')

        schema_info = mdb.get_table_schema(db, server, database, table_name)
        return jsonify({'success': True, 'schema': schema_info})
    except Exception as e:
        logging.exception(f"Schema extraction error: {e}")
        return jsonify({'success': False, 'error': str(e)})


_ALLOWED_PROXYSQL_CMD = re.compile(r'^\s*(LOAD|SAVE|SELECT\s+CONFIG)\b', re.IGNORECASE)

@app.route('/api/execute_proxysql_command', methods=['POST'])
@login_required
def api_execute_proxysql_command():
    """
    Execute allowed ProxySQL administrative commands submitted via the request form.
    
    Reads the 'sql' form field, validates that each statement is an allowed ProxySQL administrative command (e.g., LOAD, SAVE, or SELECT CONFIG), executes the commands against the server from the session (default 'proxysql'), and returns execution status.
    
    Returns:
        dict: JSON-serializable object: `{'success': True}` on success; `{'success': False, 'error': <message>}` on failure or validation error.
    """
    try:
        sql = request.form.get('sql', '')
        if not sql:
            return jsonify({'success': False, 'error': 'SQL command required'})

        statements = [s.strip() for s in sql.split(';') if s.strip()]
        if not statements or not all(_ALLOWED_PROXYSQL_CMD.match(s) for s in statements):
            logging.warning(f"Rejected disallowed command in execute_proxysql_command: {sql[:200]}")
            return jsonify({'success': False, 'error': 'Only ProxySQL LOAD/SAVE administrative commands are allowed'})

        # Get server from session
        server = session.get('server') or mdb.get_default_server()

        if mdb.get_read_only(server) or session.get('role') == 'readonly':
            return jsonify({'success': False, 'error': 'Server is in read-only mode'})

        # Execute the SQL commands
        error = mdb.execute_change(db, server, sql)

        if error:
            # Convert error to string if it's an exception object
            error_msg = str(error) if error else 'Unknown error'
            logging.error(f"ProxySQL command execution error: {error_msg}")
            return jsonify({'success': False, 'error': error_msg})
        else:
            logging.info(f"ProxySQL command executed successfully: {sql[:100]}")
            return jsonify({'success': True})

    except Exception as e:
        logging.exception(f"API error in execute_proxysql_command: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/<server>/query_history/')
@login_required
def query_history(server):
    session['server'] = server
    session['servers'] = mdb.get_servers()
    if server not in session.get('dblist', {}):
        session['dblist'] = session.get('dblist', {})
        session['dblist'].update(mdb.get_all_dbs_and_tables(db, server))
    session['misc'] = mdb.get_config(config)['misc']
    session['read_only'] = mdb.get_read_only(server)
    if session.get('role') == 'readonly':
        session['read_only'] = True
    history = mdb.load_query_history(server)
    history.reverse()
    return render_template("query_history.html", server=server, history=history)


@app.route('/api/clear_query_history', methods=['POST'])
@login_required
def api_clear_query_history():
    data = request.get_json(silent=True) or {}
    server = data.get('server', session.get('server'))
    if server not in mdb.get_servers():
        return jsonify({'success': False, 'error': 'Invalid server'}), 400
    if mdb.get_read_only(server) or session.get('role') == 'readonly':
        abort(403)
    mdb.clear_query_history(server)
    session['history'] = []
    return jsonify({'success': True})


@app.errorhandler(Exception)
def handle_exception(e):
    """
    Render an error page for an exception.

    Parameters:
        e (Exception): The exception to display on the error page; passed to the template as `error`.

    Returns:
        tuple: A rendered error page response and the HTTP status code 500.
    """
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e.get_response()
    logging.exception(e)
    return render_template("error.html", error=e), 500


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', debug=debug)

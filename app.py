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
from collections import defaultdict
from flask import Flask, render_template, request, session, url_for, flash, redirect, jsonify
from functools import wraps
import re
import os
import tempfile
import yaml
import mdb

app = Flask(__name__)

config = "config/config.yml"


def _atomic_write(path, content):
    """Write content to path atomically via a temp file in the same directory."""
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


db = defaultdict(lambda: defaultdict(dict))

# read/apply the flask config from the config file
flask_custom_config = mdb.get_config(config)
for key in flask_custom_config['flask']:
    app.config[key] = flask_custom_config['flask'][key]


mdb.logging.debug(flask_custom_config)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('You must be logged in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    session.clear()
    message=""
    admin_user = mdb.get_config(config)['auth']['admin_user']
    admin_password = mdb.get_config(config)['auth']['admin_password']

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if admin_user == username and admin_password == password:
            session['logged_in'] = True
            return redirect(url_for('render_list_dbs'))
        message="Invalid credentials!"
    return render_template("login.html", message=message)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def render_list_dbs():
    try:
        server = mdb.get_config(config)['global']['default_server']
        session['history'] = []
        session['server'] = server
        session['dblist'] = mdb.get_all_dbs_and_tables(db, server)
        session['servers'] = mdb.get_servers()
        session['read_only'] = mdb.get_read_only(server)
        session['misc'] = mdb.get_config(config)['misc']

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
        session['table'] = table
        session['database'] = database
        session['misc'] = mdb.get_config(config)['misc']
        session['read_only'] = mdb.get_read_only(server)
        content = mdb.get_table_content(db, server, database, table)
        mdb.process_table_content(table,content)
        return render_template("show_table_info.html", content=content)
    except Exception as e:
        raise ValueError(e)

@app.route('/<server>/<database>/<table>/sql/', methods=['GET', 'POST'])
@login_required
def render_change(server, database, table):
    try:
        error = ""
        message = ""
        ret = ""
        session['sql'] = request.form["sql"]


        mdb.logging.debug(session['history'])
        select = re.match(r'^SELECT.*FROM.*$', session['sql'], re.M | re.I)
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
        raise ValueError(e)


@app.route('/settings/ui_save/', methods=['POST'])
@login_required
def settings_ui_save():
    """Save settings from UI form"""
    try:
        # Back up the config file first
        with open(config, "r") as src, open(config + ".bak", "w") as dest:
            dest.write(src.read())

        # Get form data
        form_data = request.form.to_dict()

        # Build YAML config from form data
        yaml_config = mdb.form_data_to_yaml(form_data)

        # Write to config file
        _atomic_write(config, yaml_config)

        return jsonify({'success': True, 'message': 'Settings saved successfully'})
    except Exception as e:
        logging.exception(f"Error saving settings from UI: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/settings/load_ui/', methods=['GET'])
@login_required
def settings_load_ui():
    """Load settings in UI format"""
    try:
        config_data = mdb.get_config(config)
        return jsonify({'success': True, 'config': config_data})
    except Exception as e:
        logging.exception(f"Error loading config for UI: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/settings/export/', methods=['GET'])
@login_required
def settings_export():
    """Export configuration as YAML"""
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
    """Import configuration from uploaded YAML"""
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


@app.route('/proxysql/config_diff/', methods=['GET', 'POST'])
@login_required
def render_config_diff():
    """Render Configuration Diff page"""
    return render_template('config_diff.html')


@app.route('/proxysql/config_diff/get', methods=['POST'])
@login_required
def get_config_diff():
    """Get configuration differences across Disk/Memory/Runtime"""
    try:
        diff_data = mdb.get_config_diff()
        return jsonify({'success': True, 'diff': diff_data})
    except Exception as e:
        logging.exception(f"Error getting config diff: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/update_config_skip_variables', methods=['POST'])
@login_required
def update_config_skip_variables():
    """Update config_diff_skip_variable in config.yml"""
    try:
        data = request.get_json()
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
    try:
        data = request.get_json()
        server = data['server']
        database = data['database']
        table = data['table']
        row_index = data['rowIndex']
        column_names = data['columnNames']
        row_data = data['data']

        if mdb.get_read_only(server) or table.startswith('runtime_'):
            return jsonify({'success': False, 'error': 'table is read-only'}), 403

        logging.debug("=" * 80)
        logging.debug("API REQUEST: /api/update_row")
        logging.debug("=" * 80)
        logging.debug(f"Server: {server}")
        logging.debug(f"Database: {database}")
        logging.debug(f"Table: {table}")
        logging.debug(f"Row Index: {row_index}")
        logging.debug(f"Column Names: {column_names}")
        logging.debug(f"Row Data: {row_data}")
        logging.debug("=" * 80)

        result = mdb.update_row(db, server, database, table, row_index, column_names, row_data)
        logging.debug(f"Update result: {result}")
        return jsonify(result)
    except Exception as e:
        logging.exception(f"API error in update_row: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/delete_row', methods=['POST'])
@login_required
def api_delete_row():
    try:
        data = request.get_json()
        server = data['server']
        database = data['database']
        table = data['table']
        row_index = data['rowIndex']

        if mdb.get_read_only(server) or table.startswith('runtime_'):
            return jsonify({'success': False, 'error': 'table is read-only'}), 403

        logging.debug("=" * 80)
        logging.debug("API REQUEST: /api/delete_row")
        logging.debug("=" * 80)
        logging.debug(f"Server: {server}")
        logging.debug(f"Database: {database}")
        logging.debug(f"Table: {table}")
        logging.debug(f"Row Index: {row_index}")
        logging.debug("=" * 80)

        result = mdb.delete_row(db, server, database, table, row_index)
        logging.debug(f"Delete result: {result}")
        return jsonify(result)
    except Exception as e:
        logging.exception(f"API error in delete_row: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/insert_row', methods=['POST'])
@login_required
def api_insert_row():
    try:
        data = request.get_json()
        server = data['server']
        database = data['database']
        table = data['table']
        column_names = data['columnNames']
        row_data = data['data']

        if mdb.get_read_only(server) or table.startswith('runtime_'):
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
    """Get table schema including CHECK constraints and default values."""
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
    """Execute ProxySQL administrative commands (LOAD/SAVE)."""
    try:
        sql = request.form.get('sql', '')
        if not sql:
            return jsonify({'success': False, 'error': 'SQL command required'})

        statements = [s.strip() for s in sql.split(';') if s.strip()]
        if not statements or not all(_ALLOWED_PROXYSQL_CMD.match(s) for s in statements):
            logging.warning(f"Rejected disallowed command in execute_proxysql_command: {sql[:200]}")
            return jsonify({'success': False, 'error': 'Only ProxySQL LOAD/SAVE administrative commands are allowed'})

        # Get server from session
        server = session.get('server', 'proxysql')

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


@app.errorhandler(Exception)
def handle_exception(e):
    print(e)
    return render_template("error.html", error=e), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', use_debugger=True)

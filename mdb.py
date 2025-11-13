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
from datetime import datetime
import os

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

sql_get_databases = "show databases"
sql_show_table_content = "select * from %s.%s order by 1;"
sql_show_tables = "show tables from %s;"

def get_config(config="config/config.yml"):
    logging.debug("Using file: %s" % (config))
    try:
        with open(config, 'r') as yml:
            cfg = yaml.safe_load(yml)
        cfg = _apply_env_overrides(cfg)
        return cfg
    except Exception as e:
        raise ValueError("Error opening or parsing the file: %s" % config)


def _apply_env_overrides(cfg):
    """
    Override values from config.yml with environment variables when provided.
    """
    if not cfg:
        return cfg

    # Override admin UI credentials
    admin_user = os.getenv("PROXYWEB_ADMIN_USER")
    admin_password = os.getenv("PROXYWEB_ADMIN_PASSWORD")
    if admin_user or admin_password:
        cfg.setdefault('auth', {})
        if admin_user:
            logging.debug("Overriding admin user from environment variable.")
            cfg['auth']['admin_user'] = admin_user
        if admin_password:
            logging.debug("Overriding admin password from environment variable.")
            cfg['auth']['admin_password'] = admin_password

    # Override per-server DSN credentials and connection parameters
    for server_name, server_content in cfg.get('servers', {}).items():
        prefix = f"PROXYWEB_SERVER_{server_name.upper()}"
        user = os.getenv(f"{prefix}_USER")
        password = os.getenv(f"{prefix}_PASSWORD")
        host = os.getenv(f"{prefix}_HOST")
        port = os.getenv(f"{prefix}_PORT")
        database = os.getenv(f"{prefix}_DATABASE")

        if not any([user, password, host, port, database]):
            continue

        for dsn in server_content.get('dsn', []):
            if user:
                logging.debug("Overriding user for server %s from environment variable.", server_name)
                dsn['user'] = user
            if password:
                logging.debug("Overriding password for server %s from environment variable.", server_name)
                dsn['passwd'] = password
            if host:
                logging.debug("Overriding host for server %s from environment variable.", server_name)
                dsn['host'] = host
            if port:
                logging.debug("Overriding port for server %s from environment variable.", server_name)
                dsn['port'] = port
            if database:
                logging.debug("Overriding database for server %s from environment variable.", server_name)
                dsn['db'] = database

    return cfg


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
                if table['tables'] not in table_exception_list:
                    all_dbs[server][i['name']].append(table['tables'])
        db['cnf']['servers'][server]['cur'].close()
        return all_dbs
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        raise ValueError(e)


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
        logging.debug(sql)
        logging.debug(server)
        dsn = get_config()['servers'][server]['dsn'][0]
        cmd = ('mysql -h %s -P %s -u %s -p%s main   -e "%s" ' % (dsn['host'], dsn['port'], dsn['user'], dsn['passwd'], sql))
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()

        return stderr.decode().replace("mysql: [Warning] Using a password on the command line interface can be insecure.\n",'')
    except (mysql.connector.Error, mysql.connector.Warning) as e:
        return e


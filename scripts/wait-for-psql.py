#!/usr/bin/env python3
import argparse
import psycopg2
import sys
import time
import os

def get_arg_env(arg_val, env_var, default=None, required=False):
    if arg_val is not None:
        return arg_val
    var = os.environ.get(env_var)
    if var is not None:
        return var
    if default is not None:
        return default
    if required:
        print(f"ERROR: Missing required config: either argument or env variable: '{env_var}' must be set.", file=sys.stderr)
        sys.exit(2)
    return None

def wait_for_postgres(db_host, db_port, db_user, db_password, db_name, timeout):
    start_time = time.time()
    error = None
    print(f"Connecting to DB: {db_user}@{db_host}:{db_port}/{db_name}", file=sys.stderr)
    while(time.time() - start_time) < args.timeout:
        try:
            conn = psycopg2.connect(
                    user = db_user,
                    host = db_host,
                    port = db_port,
                    password = db_password,
                    dbname = db_name
            )
            conn.close()
            print("Database connection successfull.")
            return 0
        except psycopg2.OperationalError as e:
            error = e
            print(f"Waiting for PostgreSQL... {e}", file=sys.stderr)
            time.sleep(1)
    print(f"Database connection failure: {error}", file=sys.stderr)
    return 1

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('--db_host')
    arg_parser.add_argument('--db_port', type=int)
    arg_parser.add_argument('--db_user')
    arg_parser.add_argument('--db_password')
    arg_parser.add_argument('--db_name')
    arg_parser.add_argument('--timeout', type=int, default=10)
    args = arg_parser.parse_args()

    db_host = get_arg_env(args.db_host, 'ODOO_DB_HOST', required=True)
    db_port = get_arg_env(args.db_port, 'ODOO_DB_PORT', required=True)
    db_user = get_arg_env(args.db_user, 'ODOO_DB_USER', required=True)
    db_password = get_arg_env(args.db_password, 'ODOO_DB_PASSWORD', required=True)
    db_name = get_arg_env(args.db_name, 'ODOO_DB_NAME', required=True)
    timeout = get_arg_env(args.timeout, 'POSTGRES_WAIT_TIMEOUT', default=10)

    sys.exit(wait_for_postgres(db_host, db_port, db_user, db_password, db_name, int(timeout)))

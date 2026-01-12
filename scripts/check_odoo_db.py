import sys
import os
import psycopg2
from configparser import ConfigParser

ODOO_CONF = "/srv/odoo/etc/odoo.conf"

def get_db_param(conf_key, env_name, config, conf_section="options"):
    # First check in  odoo.conf
    if config and config.has_option(conf_section, conf_key):
        value = config.get(conf_section, conf_key)
        if value:
            return value
    # Use ENV variable as fallback
    value = os.environ.get(env_name)
    if value:
        return value
    return None


config = ConfigParser()
if os.path.isfile(ODOO_CONF):
    config.read(ODOO_CONF)
else:
    config = None

db_host = get_db_param("db_host", "ODOO_DB_HOST", config)
db_port = get_db_param("db_port", "ODOO_DB_PORT", config)
db_name = get_db_param("db_name", "ODOO_DB_NAME", config)
db_user = get_db_param("db_user", "ODOO_DB_USER", config)
db_pass = get_db_param("db_password", "ODOO_DB_PASSWORD", config)

missing = [p for p in ['db_host', 'db_port', 'db_name', 'db_user', 'db_pass'] if locals()[p] is None]
if missing:
    print("check_odoo_db.py (ERROR): Missing DB params: ", ", ".join(missing), file=sys.stderr)
    sys.exit(2)

try:
    conn = psycopg2.connect(
            host=db_host,
            port=int(db_port),
            dbname=db_name,
            user=db_user,
            password=db_pass
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT EXISTS(
            SELECT 1 FROM information_schema.tables
            WHERE table_name='res_company'
        );
    """)
    exists, = cur.fetchone()
    cur.close()
    conn.close()
    sys.exit(0 if exists else 1)

except Exception as e:
    print("check_odoo_db.py (ERROR): ", e, file=sys.stderr)
    sys.exit(2)

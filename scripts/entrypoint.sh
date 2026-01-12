#!/bin/bash
set -e

odoo_bin='/srv/odoo/odoo/odoo-bin'
odoo_conf='/srv/odoo/etc/odoo.conf'
custom_requirements='/srv/odoo/etc/custom_requirements.txt'
python3='/usr/bin/python3'
pip='/usr/bin/pip'
wait_for_psql='/srv/odoo/scripts/wait-for-psql.py'
check_odoo_db='/srv/odoo/scripts/check_odoo_db.py'
gen_odoo_conf='/srv/odoo/scripts/odoo_conf.py'

# Check mandatory POSTGRES_ variables (hard fail if missing)
: "${ODOO_DB_HOST:?ODOO_DB_HOST must be set}"
: "${ODOO_DB_PORT:?ODOO_DB_PORT must be set}"
: "${ODOO_DB_USER:?ODOO_DB_USER must be set}"
: "${ODOO_DB_PASSWORD:?ODOO_DB_PASSWORD must be set}"
: "${ODOO_DB_NAME:?ODOO_DB_NAME must be set}"

echo "$ODOO_DB_HOST:$ODOO_DB_PORT:$ODOO_DB_NAME:$ODOO_DB_USER:$ODOO_DB_PASSWORD" > "${HOME}/.pgpass"
chmod 600 "${HOME}/.pgpass"

# Generate the Odoo config from default + ENV overrides
echo "Generating ${odoo_conf} ..."
CLI_ARGS=$($python3 $gen_odoo_conf)

# Process custom requirements
echo "Processing custom requirements"
if [ -f ${custom_requirements} ]; then
	$pip install --break-system-packages -r ${custom_requirements};
fi

echo "Waiting for PostgreSQL ($ODOO_DB_HOST:$ODOO_DB_PORT) ..."
$python3 $wait_for_psql --db_host "$ODOO_DB_HOST" --db_port "$ODOO_DB_PORT" --db_user "$ODOO_DB_USER" --db_password "$ODOO_DB_PASSWORD" --timeout=30

echo "Checking PostgreSQL for Odoo readiness ..."
if $python3 $check_odoo_db; then
    echo " => database already initialized for Odoo"
else
    echo " => database not ready for Odoo. Initializing ..."
    $odoo_bin -d "${ODOO_DB_NAME}" -c ${odoo_conf} $CLI_ARGS --init base,web
fi

echo "Checking for Update request on Environment Variable"
if [ "${UPDATE_ODOO_ON_START}" = "1" ]; then
	echo "*** request to update installation on start! ***"
	exec $odoo_bin -d "${ODOO_DB_NAME}" -c ${odoo_conf} $CLI_ARGS --update=all
fi

case "$1" in
    update)
	shift
	exec $odoo_bin -d "${ODOO_DB_NAME}" -c ${odoo_conf} ${CLI_ARGS} --update=all "$@"
	;;
    -- | odoo)
        shift
        if [[ "$1" == "scaffold" ]]; then
            exec $odoo_bin -c ${odoo_conf} "$@"
        else
            exec $odoo_bin -c ${odoo_conf} $CLI_ARGS "$@"
        fi
        ;;
    -*)
        exec $odoo_bin -c ${odoo_conf} $CLI_ARGS "$@"

        ;;
    *)
        exec "$@"
        ;;
esac


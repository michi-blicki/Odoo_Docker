import os
import re
import sys
from configparser import ConfigParser

DEFAULT_CONF = "/srv/odoo/etc/odoo.conf.default"
TARGET_CONF  = "/srv/odoo/etc/odoo.conf"

config = ConfigParser()
config.read(DEFAULT_CONF)

env_conf = {}
cli_args = []

for key,val in os.environ.items():
    if not key.startswith("ODOO_"):
        continue

    param = key[5:]
    param_lc = param.lower()
    print(f"odoo_conf.py: Processing key: {key} / param: {param_lc}", file=sys.stderr)
    env_conf[param_lc] = val.strip()

if "options" not in config.sections():
    config.add_section("options")

for key,val in env_conf.items():
    config.set("options", key, val)

with open(TARGET_CONF, "w") as f:
    config.write(f)

print(" ".join(cli_args))

#!/bin/bash
#

/usr/bin/docker build . -t blicki/odoo:18.0-PROD --build-arg BUILD_TYPE=PROD --no-cache

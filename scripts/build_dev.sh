#!/bin/bash
#

/usr/bin/docker build . -t blicki/odoo:18.0-DEV --build-arg BUILD_TYPE=DEV --no-cache --progress=plain

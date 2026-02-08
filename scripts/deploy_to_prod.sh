#!/bin/bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Deployment Script: Odoo 18.0 Image to Docker Swarm (odoo-prod)
# Autor: blicki / 2026-02-03
# Description: Deploy new image to all swarm nodes and update service
# Swarm nodes: lxmc10 (local manager), lxerp01, lxcs01
# Image storage: /srv/gfs-backup/odoo-images (GlusterFS shared)
# Compose file: /srv/gfs-storage/odoo-prod/docker-compose.yml (GlusterFS shared)
# ------------------------------------------------------------------------------

SWARM_NODES=("lxerp01" "lxcs01")
LOCAL_NODE="lxmc10"
REMOTE_USER="blicki"
IMAGES_PATH="/srv/gfs-backup/odoo-images"
COMPOSE_PATH="/srv/gfs-storage/odoo-prod"
IMAGE_NAME="blicki/odoo"
IMAGE_TAG="18.0-PROD"
CURRENT_TAR="${IMAGES_PATH}/blicki_odoo_${IMAGE_TAG}_current.tar"
SWARM_STACK="odoo-prod"
SWARM_SERVICE="app"

echo "=== (1/5) Backup and archive images  ==="
cd "${IMAGES_PATH}"

# Archive existing image files with timestamp
for f in blicki_odoo_${IMAGE_TAG}_current.tar blicki_odoo_${IMAGE_TAG}_*.tar; do
    [ -f "$f" ] || continue
    # Use modification date in filename format YYYY-MM-DD_HH-MM
    TIMESTAMP=$(date -r "$f" +%Y-%m-%d_%H-%M)
    ARCHIVED_FILE="blicki_odoo_${IMAGE_TAG}_${TIMESTAMP}.tar"

    # Prevent overwrites by adding _dup suffix if needed
    [ -e "$ARCHIVED_FILE" ] && ARCHIVED_FILE="${ARCHIVED_FILE}_dup"

    echo "   -> Archive $f -> $ARCHIVED_FILE.gz"
    mv "$f" "$ARCHIVED_FILE"
    gzip -9 "$ARCHIVED_FILE"
done

echo "=== (2/5) Export current docker image to file  ==="
docker save -o "${CURRENT_TAR}" "${IMAGE_NAME}:${IMAGE_TAG}"
echo "   -> $(ls -lh "${CURRENT_TAR}")"

echo "=== (3/5) Stop odoo-prod service locally ==="
docker service rm ${SWARM_STACK}_${SWARM_SERVICE} || echo "   -> Service not found, skipping removal"
echo "   -> ${SWARM_STACK}_${SWARM_SERVICE} stopped"

echo "=== (4/5) Load new image on all swarm nodes ==="
for NODE in "${SWARM_NODES[@]}"; do
    echo "   -> Loading image on $NODE..."
    ssh -o BatchMode=yes "${REMOTE_USER}@${NODE}" <<EOF
set -e
docker load -i ${IMAGES_PATH}/blicki_odoo_${IMAGE_TAG}_current.tar
echo "   -> Image loaded on $NODE"
EOF
done

echo "=== (5/5) Deploy odoo-prod service locally ==="
docker stack deploy -c ${COMPOSE_PATH}/docker-compose.yml ${SWARM_STACK}
echo "   -> ${SWARM_STACK} service deployed"

echo "✅ Deployment to Docker Swarm completed."


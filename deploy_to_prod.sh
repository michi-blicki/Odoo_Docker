#!/bin/bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Deployment Script: Odoo 18.0 Image Transfer lxmc10 -> lxerp01
# Autor: blicki / 2026-01-07
# ------------------------------------------------------------------------------

REMOTE_HOST="lxerp01"
REMOTE_USER="blicki"
REMOTE_PATH="/srv/backup/odoo-images"
LOCAL_PATH="/srv/backup/odoo-images"
IMAGE_NAME="blicki/odoo"
IMAGE_TAG="18.0-PROD"
CURRENT_TAR="${LOCAL_PATH}/blicki_odoo_${IMAGE_TAG}_current.tar"

echo "=== (1/7) Archivierung alter lokaler Images ==="
cd "${LOCAL_PATH}"

# Alle bestehenden .tar oder .tar.gz-Dateien durchgehen
for f in blicki_odoo_${IMAGE_TAG}_current.tar blicki_odoo_${IMAGE_TAG}_*.tar; do
    [ -f "$f" ] || continue
    # Änderungsdatum im Dateinamen-Format YYYY-MM-DD_HH-MM
    ERSTELL_DATUM=$(date -r "$f" +%Y-%m-%d_%H-%M)
    NEUFILE="blicki_odoo_${IMAGE_TAG}_${ERSTELL_DATUM}.tar"

    # Wenn Zielname existiert: Suffix für Sicherheit anhängen
    [ -e "$NEUFILE" ] && NEUFILE="${NEUFILE}_dup"

    echo "   -> Archiviere $f -> $NEUFILE.gz"
    mv "$f" "$NEUFILE"
    gzip -9 "$NEUFILE"
done

echo "=== (2/7) Neues Docker-Image exportieren ==="
docker save -o "${CURRENT_TAR}" "${IMAGE_NAME}:${IMAGE_TAG}"
echo "   -> $(ls -lh "${CURRENT_TAR}")"

echo "=== (3/7) Remote: 'current' nach 'previous' verschieben ==="
ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<EOF
set -e
REMOTE_PATH="${REMOTE_PATH}"
IMAGE_TAG="${IMAGE_TAG}"
if [ -f "${REMOTE_PATH}/blicki_odoo_\${IMAGE_TAG}_current.tar" ]; then
    mv -f "${REMOTE_PATH}/blicki_odoo_\${IMAGE_TAG}_current.tar" \
          "${REMOTE_PATH}/blicki_odoo_\${IMAGE_TAG}_previous.tar"
fi
EOF

echo "=== (4/7) Übertrage neues Image per SCP ==="
scp -p "${CURRENT_TAR}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"

echo "=== (5/7) Remote: Lade neues Image in Docker auf ${REMOTE_HOST} ==="
ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" <<EOF
set -e
IMAGE_TAG="${IMAGE_TAG}"
docker load -i /srv/backup/odoo-images/blicki_odoo_\${IMAGE_TAG}_current.tar
EOF

echo "=== (6/7) Remote: Stoppe odoo-prod-app ==="
ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" <<'EOF'
set -e
cd /srv/docker/odoo-prod
docker compose stop odoo-prod-app || true
EOF

echo "=== (7/7) Remote: Starte odoo-prod-app mit neuem Image ==="
ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" <<'EOF'
set -e
cd /srv/docker/odoo-prod
docker compose up -d odoo-prod-app
EOF

echo "✅ Deployment erfolgreich abgeschlossen."


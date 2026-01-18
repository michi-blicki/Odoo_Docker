#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odoo Docker Backup Script
===============================================================

Snapshot Backup process for Odoo docker environment to create
daily, hourly or manually created backups. Specify parameters
within the script's configuration part.

Author:                 Michael Blickenstorfer <michi@blicki.ch>
Creation Date:          2026-01-14
Last modification date: 2026-01-14
Version:                2.0

History / Change Log
---------------------------------------------------------------
"""

import os
import sys
import subprocess
import json
import tarfile
import gzip
import shutil
import hashlib
import datetime
import logging
from pathlib import Path

# =========================================================
# === CONFIGURATION =======================================
# =========================================================

# Base directories
dir_backup_base    = "/srv/backup/odoo-prod"
dir_backup_hourly  = f"{dir_backup_base}/hourly"
dir_backup_daily   = f"{dir_backup_base}/daily"
dir_backup_manual  = f"{dir_backup_base}/manual"
dir_backup_meta    = f"{dir_backup_base}/meta"
dir_backup_images  = "/srv/backup/odoo-images"

# Docker-Image Tag
docker_image_tag = "blicki/odoo:18.0-PROD"

# Database configuration
db_container = "odoo-prod-db"
db_user      = "odoo"
db_name      = "odoo"

# Additional host directories to be included
backup_directories = [
    "/srv/docker/odoo-prod/odoo_local_share",
    "/srv/docker/odoo-prod/odoo_custom_addons",
    "/srv/docker/odoo-prod/odoo_data",
]

# Retention configuration
retention_daily  = 7   # Tage; 0 = behalten
retention_manual = 0   # Tage; 0 = behalten

# Compress Tool for Image-Backup: gzip | bzip2 | xz | none
image_compression = "gzip"

# Logging configuration
log_file  = f"{dir_backup_meta}/odoo_backup.log"
log_level = logging.INFO   # oder logging.DEBUG für Detailanalyse

# =========================================================
# === LOGGING SETUP =======================================
# =========================================================

logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)]
)

# =========================================================
# === HELPER FUNCTIONS ====================================
# =========================================================

def ensure_directories():
    """Stellt sicher, dass alle nötigen Verzeichnisse existieren."""
    for d in [
        dir_backup_hourly, dir_backup_daily,
        dir_backup_manual, dir_backup_meta,
        dir_backup_images
    ]:
        Path(d).mkdir(parents=True, exist_ok=True)

def run_cmd(cmd, capture_output=False):
    """Führt Shell-Befehl sicher aus."""
    logging.debug(f"Run CMD: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=capture_output, text=True, check=True)
        return result.stdout.strip() if capture_output else None
    except subprocess.CalledProcessError as e:
        logging.error(f"Fehler bei Kommando {' '.join(cmd)}: {e}")
        sys.exit(1)

def get_image_id():
    """Liest die aktuelle Image-ID des Docker-Tags."""
    return run_cmd(
        ["docker", "image", "inspect", docker_image_tag, "--format", "{{.Id}}"],
        capture_output=True
    )

def compute_sha256(path):
    """Berechnet SHA256-Checksumme einer Datei."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

def set_backup_permissions(path):
    """Setzt Ownership und Zugriffsrechte für Backup-Dateien."""
    import grp, pwd
    try:
        # gewünschte Gruppe
        group_name = "odoo"
        gid = grp.getgrnam(group_name).gr_gid
        uid = pwd.getpwnam("root").pw_uid
        os.chown(path, uid, gid)
        os.chmod(path, 0o640)
        logging.debug(f"Rechte gesetzt für {path} (root:{group_name}, 0640)")
    except KeyError:
        logging.warning("Gruppe 'odoo' nicht gefunden – bitte manuell anlegen.")
    except Exception as e:
        logging.warning(f"Konnte Rechte nicht setzen für {path}: {e}")

# =========================================================
# === META / INDEX FUNCTIONS===============================
# =========================================================
def reconcile_manual_backups(backups_index_file):
    """
    Prüft alle manuellen Backups im Dateisystem und passt den Index an:
    - Entfernt Einträge, deren Dateien fehlen
    - Fügt neue manuelle Backup-Dateien hinzu, falls sie nicht im Index stehen
    """
    index_data = load_json(backups_index_file)
    if not isinstance(index_data, list):
        logging.warning("Backup-Index leer oder fehlerhaft – Abgleich übersprungen.")
        return

    manual_dir = Path(dir_backup_manual)
    if not manual_dir.exists():
        logging.info(f"Manueller Backup-Ordner {manual_dir} nicht vorhanden.")
        return

    existing_files = {str(p.resolve()) for p in manual_dir.glob("backup_manual_*.tar.gz")}
    updated = []
    removed = 0
    added = 0

    # 1️⃣ – Entferne verwaiste Indexeinträge
    for e in index_data:
        if e.get("type") == "manual":
            archive_path = e.get("archive_path")
            if not archive_path or not os.path.exists(archive_path):
                logging.debug(
                    f"Entferne Index-Eintrag für nicht mehr vorhandenes manuelles Backup: {archive_path}"
                )
                removed += 1
                continue
        updated.append(e)

    # 2️⃣ – Ergänze neue manuelle Backups ins Index, falls noch nicht enthalten
    known_paths = {item.get("archive_path") for item in updated}
    for file_path in existing_files:
        if file_path not in known_paths:
            size_mb = Path(file_path).stat().st_size / 1024 / 1024

            # Versuche, Metadaten aus meta.json im Backup zu lesen
            meta_in_archive = None
            try:
                with tarfile.open(file_path, "r:gz") as tar:
                    member = tar.getmember("meta.json")
                    with tar.extractfile(member) as f:
                        meta_in_archive = json.load(f)
            except Exception as e:
                logging.debug(f"meta.json konnte nicht gelesen werden aus {file_path}: {e}")

            # Image-ID & Image-Pfad aus meta.json übernehmen
            image_id_from_meta = (
                meta_in_archive.get("image_id") if meta_in_archive else "unknown"
            )
            image_path_from_meta = (
                meta_in_archive.get("image_path") if meta_in_archive else "unknown"
            )

            # Neuen Eintrag erzeugen
            entry = {
                "type": "manual",
                "timestamp": datetime.datetime.fromtimestamp(
                    Path(file_path).stat().st_mtime
                ).isoformat(),
                "archive_path": file_path,
                "size_mb": round(size_mb, 2),
                "image_tag": docker_image_tag,
                "image_id": image_id_from_meta,
                "image_path": image_path_from_meta,
                "sha256": compute_sha256(file_path),
            }
            updated.append(entry)
            added += 1
            logging.info(f"Füge neues manuelles Backup dem Index hinzu: {file_path}")

    # 3️⃣ – Index speichern
    updated = sorted(updated, key=lambda e: e["timestamp"], reverse=True)
    with open(backups_index_file, "w") as f:
        json.dump(updated, f, indent=4)

    if removed or added:
        logging.info(f"Index-Abgleich abgeschlossen: {removed} entfernt, {added} hinzugefügt.")
    else:
        logging.info("Index-Abgleich: keine Änderungen erforderlich.")



def reconcile_all_backups(index_file):
    """
    Synchronisiert den Backup-Index mit dem tatsächlichen Inhalt aller Backup-Ordner.
    - Fügt neue Archive hinzu
    - Entfernt verwaiste Einträge
    - Ruft anschließend cleanup_unused_images() auf
    """

    logging.info("=== Starte vollständigen Backup-Reconcile-Lauf ===")

    # Alle aktuell vorhandenen Archive im Filesystem einlesen
    current_archives = {}
    for btype, folder in {
        "hourly": dir_backup_hourly,
        "daily":  dir_backup_daily,
        "manual": dir_backup_manual
    }.items():
        folder_path = Path(folder)
        if folder_path.exists():
            for file in folder_path.glob("backup_*.tar.gz"):
                current_archives[str(file.resolve())] = {
                    "type": btype,
                    "path": str(file.resolve())
                }

    # Aktuellen Index lesen
    index_data = load_json(index_file)
    if not isinstance(index_data, list):
        index_data = []
    known_paths = {e.get("archive_path") for e in index_data}

    cleaned_index = []
    removed = 0
    added = 0

    # 1️⃣ – Entferne alle Einträge, deren Dateien nicht mehr existieren
    for e in index_data:
        path = e.get("archive_path")
        if path and os.path.exists(path):
            cleaned_index.append(e)
        else:
            logging.debug(f"Entferne verwaisten Indexeintrag: {path}")
            removed += 1

    # 2️⃣ – Ergänze neue Archive, die noch nicht im Index stehen
    for path, info in current_archives.items():
        if path not in known_paths:
            size_mb = Path(path).stat().st_size / 1024 / 1024
            meta_in_archive = None
            try:
                with tarfile.open(path, "r:gz") as tar:
                    member = tar.getmember("meta.json")
                    with tar.extractfile(member) as f:
                        meta_in_archive = json.load(f)
            except Exception as e:
                logging.debug(f"meta.json konnte nicht gelesen werden aus {path}: {e}")

            image_id_from_meta = None
            if meta_in_archive and "image_id" in meta_in_archive:
                image_id_from_meta = meta_in_archive["image_id"]
            else:
                image_id_from_meta = "unknown"
            entry = {
                "type": info["type"],
                "timestamp": datetime.datetime.fromtimestamp(Path(path).stat().st_mtime).isoformat(),
                "archive_path": path,
                "size_mb": round(size_mb, 2),
                "image_tag": docker_image_tag,
                "image_id": image_id_from_meta,
                "image_path": meta_in_archive.get("image_path", "unknown"),
                "sha256": compute_sha256(path)
            }
            cleaned_index.append(entry)
            added += 1
            logging.info(f"Füge neues Backup hinzu: {path}")

    # 3️⃣ – Index sortieren und speichern
    cleaned_index = sorted(cleaned_index, key=lambda e: e["timestamp"], reverse=True)
    with open(index_file, "w") as f:
        json.dump(cleaned_index, f, indent=4)

    logging.info(f"Reconcile-Ergebnis: {added} hinzugefügt, {removed} entfernt, {len(cleaned_index)} gesamt.")
    set_backup_permissions(index_file)

    # 4️⃣ – Abschließend: Docker-Images bereinigen
    cleanup_unused_images()
    logging.info("=== Reconcile-Lauf abgeschlossen ===")

def register_backup_in_index(archive_path, backup_type, image_tag, image_id, image_path):
    """Trägt das Backup in die zentrale Index-Datei ein."""
    index_file = os.path.join(dir_backup_meta, "backups_index.json")
    index_data = []

    if os.path.exists(index_file):
        try:
            with open(index_file, "r") as f:
                index_data = json.load(f)
        except Exception:
            logging.warning("Konnte bisheriges Index-File nicht lesen – wird neu erstellt.")

    # Archivinfos
    size_mb = Path(archive_path).stat().st_size / 1024 / 1024
    entry = {
        "type": backup_type,
        "timestamp": datetime.datetime.now().isoformat(),
        "archive_path": archive_path,
        "size_mb": round(size_mb, 2),
        "image_tag": image_tag,
        "image_id": image_id,
        "image_path": image_path,
        "sha256": compute_sha256(archive_path)
    }

    index_data.append(entry)

    # -------------------------------------------------------
    # Index nach Retention bereinigen
    # -------------------------------------------------------
    now = datetime.datetime.now()
    cleaned = []
    for e in index_data:
        archive_file = e.get("archive_path")
        ts = datetime.datetime.fromisoformat(e.get("timestamp", now.isoformat()))
        age_days = (now - ts).days

        exists = archive_file and os.path.exists(archive_file)
        btype = e.get("type", "daily").lower()

        keep = True
        # Retention prüfen
        if btype == "daily" and retention_daily > 0 and age_days > retention_daily:
            keep = False
        elif btype == "manual" and retention_manual > 0 and age_days > retention_manual:
            keep = False
        elif btype == "hourly" and age_days > 2:  # nur max. 2 Tage Hourlys im Index
            keep = False

        if not exists:
            keep = False

        if keep:
            cleaned.append(e)
        else:
            logging.debug(f"Entferne alten oder ungültigen Indexeintrag: {archive_file}")

    # Neu sortieren nach Zeitstempel (jüngste zuerst)
    index_data = sorted(cleaned, key=lambda e: e["timestamp"], reverse=True)

    # -------------------------------------------------------
    # zusätzlich: nur letzte 24 Hourlys im Index
    # -------------------------------------------------------
    hourly_entries = [e for e in index_data if e["type"] == "hourly"]
    if len(hourly_entries) > 24:
        hourly_keep = hourly_entries[:24]
        index_data = [e for e in index_data if e["type"] != "hourly"] + hourly_keep

    with open(index_file, "w") as f:
        json.dump(index_data, f, indent=4)

    logging.info(f"Backup-Index aktualisiert: {index_file}")

    # Rechte neu setzen
    set_backup_permissions(archive_path)
    set_backup_permissions(index_file)

# =========================================================
# === BACKUP FUNCTIONEN ===================================
# =========================================================

def backup_database(target_dir):
    """Erstellt DB-Dump."""
    dump_path = os.path.join(target_dir, "odoo-db.sql.gz")
    cmd = ["docker", "exec", db_container, "pg_dump", "-U", db_user, db_name]
    logging.info(f"Datenbank-Dump ({db_name}) wird erstellt ...")
    with open(dump_path, "wb") as f_out:
        p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        p2 = subprocess.Popen(["gzip", "-9"], stdin=p1.stdout, stdout=f_out)
        p1.stdout.close()
        p2.communicate()
    logging.info(f"DB-Dump abgeschlossen: {dump_path}")
    return dump_path

def backup_directories_func(target_dir):
    """Sichert definierte Verzeichnisse (nur deren Inhalt) als tar.gz."""
    archives = []
    for src in backup_directories:
        name = Path(src).name
        archive_path = os.path.join(target_dir, f"{name}.tar.gz")
        logging.info(f"Sichere Inhalt von {src} ...")

        with tarfile.open(archive_path, "w:gz") as tar:
            # Nur den Inhalt (nicht das Verzeichnis selbst) hinzufügen
            for item in os.listdir(src):
                item_path = os.path.join(src, item)
                tar.add(item_path, arcname=item)
        archives.append(archive_path)
    return archives

def export_image_if_new():
    """
    Exportiert das aktuelle Docker-Image des definierten Tags,
    falls dieses Image noch nicht als komprimierte Backup-Datei vorhanden ist.

    Rückgabe:
        (image_id, image_path)
    """
    current_id = get_image_id()
    image_filename = f"odoo_image_{current_id.replace(':', '_')}.tar"
    image_tar = os.path.join(dir_backup_images, image_filename)
    # Je nach Compression wird Endung ergänzt
    if image_compression == "gzip":
        image_path = image_tar + ".gz"
    elif image_compression == "bzip2":
        image_path = image_tar + ".bz2"
    elif image_compression == "xz":
        image_path = image_tar + ".xz"
    else:
        image_path = image_tar

    # --- Prüfen, ob das Image bereits existiert
    if os.path.exists(image_path):
        logging.info(f"Docker-Image bereits vorhanden: {image_path}")
        return current_id, image_path

    # --- Exportieren
    logging.info(f"Exportiere neues Docker-Image: {docker_image_tag}")
    run_cmd(["docker", "save", "-o", image_tar, docker_image_tag])

    # --- Komprimieren falls nötig
    if image_compression == "gzip":
        logging.info("Komprimiere Docker-Image (gzip) ...")
        run_cmd(["gzip", "-9", image_tar])
    elif image_compression == "bzip2":
        logging.info("Komprimiere Docker-Image (bzip2) ...")
        run_cmd(["bzip2", "-9", image_tar])
    elif image_compression == "xz":
        logging.info("Komprimiere Docker-Image (xz) ...")
        run_cmd(["xz", "-9", image_tar])
    else:
        logging.info("Keine Kompression gewählt – Image bleibt unkomprimiert.")

    # --- Hash & Logging
    sha256 = compute_sha256(image_path)
    size_mb = Path(image_path).stat().st_size / 1024 / 1024
    logging.info(f"Docker-Image gespeichert: {image_path}")
    logging.info(f"Größe: {size_mb:.1f} MB, SHA256: {sha256[:16]}…")

    return current_id, image_path


def create_archive(backup_type, files, image_id, image_path):
    """Erstellt das Backup-Archiv mit Metadaten."""
    now = datetime.datetime.now()

    if backup_type == "hourly":
        name = f"backup_hourly_{now.strftime('%H')}.tar.gz"
        base_dir = dir_backup_hourly
    elif backup_type == "manual":
        name = f"backup_manual_{now.strftime('%Y-%m-%d_%H-%M')}.tar.gz"
        base_dir = dir_backup_manual
    else:  # daily
        name = f"backup_daily_{now.strftime('%Y-%m-%d')}.tar.gz"
        base_dir = dir_backup_daily

    archive_path = os.path.join(base_dir, name)

    dir_mappings = []
    for src in backup_directories:
        name = Path(src).name
        dir_mappings.append({
            "directory": src,
            "file": f"{name}.tar.gz"
        })

    meta = {
        "timestamp": now.isoformat(),
        "type": backup_type,
        "db": {
            "container": db_container,
            "user": db_user,
            "name": db_name
        },
        "image_tag": docker_image_tag,
        "image_id": image_id,
        "image_path": image_path,
        "directories": dir_mappings,
        "files": [os.path.basename(f) for f in files]
    }

    tmp_meta = os.path.join(base_dir, "meta.json")
    with open(tmp_meta, "w") as f:
        json.dump(meta, f, indent=4)

    with tarfile.open(archive_path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=os.path.basename(f))
        tar.add(tmp_meta, arcname="meta.json")

    os.remove(tmp_meta)
    logging.info(f"{backup_type.upper()} Backup fertig: {archive_path}")

    return archive_path

# =========================================================
# === RETENTION/DELETE ===================================
# =========================================================

def cleanup_old_backups(base_dir, retention_days):
    """Löscht alte Backups falls Retention > 0."""
    if retention_days <= 0:
        return
    now = datetime.datetime.now()
    backups = sorted(Path(base_dir).glob("backup_*.tar.gz"))
    for b in backups:
        mtime = datetime.datetime.fromtimestamp(b.stat().st_mtime)
        age = (now - mtime).days
        if age > retention_days:
            logging.info(f"Lösche altes Backup ({age} Tage): {b}")
            b.unlink()

# =========================================================
# === RESTORE FUNCTIONS ===================================
# =========================================================
def decompress_image_if_needed(image_path, output_prefix):
    """
    Ermittelt die Kompression (.gz / .bz2 / .xz / .tar) und dekomprimiert falls nötig.
    Gibt Pfad zur dekomprimierten .tar-Datei zurück.
    """
    import gzip, bz2, lzma

    # Zielpfad ergibt sich aus Prefix
    decompressed_path = f"{output_prefix}.tar"

    # Prüfen, ob entpackt werden muss
    suffix = Path(image_path).suffix.lower()

    logging.info(f"Detected image format: {suffix}")
    if suffix == ".gz":
        logging.info("Decompressing gzip image ...")
        with gzip.open(image_path, "rb") as f_in, open(decompressed_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    elif suffix == ".bz2":
        logging.info("Decompressing bzip2 image ...")
        with bz2.open(image_path, "rb") as f_in, open(decompressed_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    elif suffix == ".xz":
        logging.info("Decompressing xz image ...")
        with lzma.open(image_path, "rb") as f_in, open(decompressed_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    elif suffix == ".tar":
        logging.info("Image already uncompressed ...")
        shutil.copy(image_path, decompressed_path)
    else:
        logging.warning(f"Unknown image suffix '{suffix}' — assuming uncompressed .tar")
        shutil.copy(image_path, decompressed_path)

    return decompressed_path


def restore_backup(archive_path, target_dir, port=10014, start_container=True):
    """
    Restore a backup archive:
    - extract backup archive
    - secure import of docker image (renaming to RESTORE-<HASH>)
    - recreate host directories and database
    - optionally start new test container instances
    """

    ensure_directories()
    logging.info(f"Restore Start: {archive_path}")
    tmp_dir = Path("/tmp/odoo_restore_tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(exist_ok=True)

    # -------------------------------------------------------
    # 1. Archiv entpacken
    # -------------------------------------------------------
    logging.info("unpack backup-archiv ...")
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(tmp_dir)

    meta_file = tmp_dir / "meta.json"
    if not meta_file.exists():
        logging.error("ERROR: meta.json not found in backup.")
        sys.exit(1)

    meta = load_json(meta_file)
    image_id   = meta.get("image_id")
    image_tag  = meta.get("image_tag")
    image_tar  = meta.get('image_path')
    directories = meta.get("directories", [])
    db_meta     = meta.get("db", {})

    if not image_tar or not os.path.exists(image_tar):
        logging.error(f"Docker Image file not found: {image_tar}")
        sys.exit(1)

    logging.info(f"Found Backup: {meta.get('timestamp')} (Image-ID: {image_id})")

    # -------------------------------------------------------
    # 2. Docker-Image wiederherstellen (sicher, auch komprimiert)
    # -------------------------------------------------------
    safe_tag = f"{image_tag}-RESTORE-{image_id[7:14]}"  # z. B. blicki/odoo:18.0-PROD-RESTORE-d5773b9
    logging.info(f"Preparing secure restore of Docker image as '{safe_tag}' ...")

    if not image_tar or not os.path.exists(image_tar):
        logging.error(f"Docker image file not found: {image_tar}")
        sys.exit(1)

    # Temporäres Arbeitsfile
    tmp_prefix  = f"/tmp/odoo_image_restore_{image_id[7:17]}"
    patched_tar = f"{tmp_prefix}.tar"

    # -------------------------------------------------------
    # 2a. Falls Image komprimiert ist → dekomprimieren
    # -------------------------------------------------------
    logging.info(f"Checking image compression: {image_tar}")
    decompressed_path = None
    decompressed_path = decompress_image_if_needed(image_tar, tmp_prefix)

    # -------------------------------------------------------
    # 2b. Manifest patchen – Tag umbennen, damit produktives Tag unberührt bleibt
    # -------------------------------------------------------
    logging.info(f"Patching manifest.json to use safe tag '{safe_tag}' ...")

    # Manifest extrahieren
    with tarfile.open(patched_tar, "r") as t_in:
        t_in.extract("manifest.json", "/tmp/")
    manifest_path = "/tmp/manifest.json"

    with open(manifest_path, "r") as mf:
        manifest = json.load(mf)
        manifest[0]["RepoTags"] = [safe_tag]
    with open("/tmp/manifest_tmp.json", "w") as mf:
        json.dump(manifest, mf)

    # Manifest in TAR aktualisieren
    run_cmd(["tar", "rf", patched_tar, "-C", "/tmp", "manifest_tmp.json"])

    # Aufräumen temporärer Manifestdateien
    os.remove("/tmp/manifest.json")
    os.remove("/tmp/manifest_tmp.json")

    # -------------------------------------------------------
    # 2c. Image sicher laden
    # -------------------------------------------------------
    logging.info(f"Loading Docker image (tagged for restore): {safe_tag}")
    run_cmd(["docker", "load", "-i", patched_tar])

    # -------------------------------------------------------
    # 2d. Temporären File entfernen
    # -------------------------------------------------------
    if os.path.exists(patched_tar) and image_tar != patched_tar:
        os.remove(patched_tar)


    # -------------------------------------------------------
    # 3. Zielverzeichnisse wiederherstellen (neue Struktur aus meta["directories"])
    # -------------------------------------------------------
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    # meta["directories"] ist jetzt eine Liste von Mappings {directory, file}
    for mapping in meta.get("directories", []):
        src_dir = mapping.get("directory")
        archive_file = mapping.get("file")

        if not archive_file:
            logging.warning(f"Kein 'file'-Eintrag gefunden für {src_dir} – übersprungen.")
            continue

        archive_path = tmp_dir / archive_file
        if not archive_path.exists():
            logging.warning(f"Archiv {archive_file} fehlt im Backup-Archiv – übersprungen.")
            continue

        # Zielpfad: entweder absolute Entsprechung oder unterhalb target_dir
        # Falls du Prod→Test ersetzen möchtest:
        dest_dir = src_dir.replace("odoo-prod", Path(target_dir).name)
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)

        print(f"📦 Entpacke {archive_file} → {dest_path}")
        logging.info(f"Extracting {archive_file} → {dest_path}")

        try:
            # Entpacken mit sudo auf dem Host, falls nötig
            cmd = ["sudo", "tar", "xzf", str(archive_path), "-C", str(dest_path)]
            run_cmd(cmd)
        except SystemExit:
            logging.error(f"Fehler beim Entpacken von {archive_file}")
            sys.exit(1)

    logging.info("Alle definierten Verzeichnisse aus meta.json erfolgreich wiederhergestellt.")


    # -------------------------------------------------------
    # 4. PostgreSQL-Container für Restore
    # -------------------------------------------------------
    db_container_restore = f"{db_meta.get('container', 'odoo-prod-db')}-restore"
    logging.info(f"Start Restore PostgreSQL-Instance ({db_container_restore}) ...")

    run_cmd([
        "docker", "run", "-d",
        "--name", db_container_restore,
        "-e", f"POSTGRES_USER={db_meta.get('user','odoo')}",
        "-e", f"POSTGRES_PASSWORD={db_meta.get('user','odoo')}",
        "-e", f"POSTGRES_DB={db_meta.get('name','odoo')}",
        "-v", f"{target_dir}/pgdata:/var/lib/postgresql/data",
        "postgres:16"
    ])

    # -------------------------------------------------------
    # 5. DB-Dump wiederherstellen
    # -------------------------------------------------------
    dump_file = tmp_dir / "odoo-db.sql.gz"
    if dump_file.exists():
        logging.info("Apply database backup to restore-db ...")
        run_cmd([
            "bash", "-c",
            f"gunzip -c {dump_file} | docker exec -i {db_container_restore} psql -U {db_meta.get('user','odoo')} -d {db_meta.get('name','odoo')}"
        ])
    else:
        logging.warning("DB-dump not found!!")

    # -------------------------------------------------------
    # 6. Optional: App-Container aus wiederhergestelltem Image starten
    # -------------------------------------------------------
    if start_container:
        app_container_name = f"odoo-restore-app-{image_id[7:14]}"
        logging.info(f"Launch restore container '{app_container_name}' on port {port} ...")
        run_cmd([
            "docker", "run", "-d",
            "--name", app_container_name,
            "-p", f"{port}:8069",
            "-v", f"{target_dir}/odoo_local_share:/var/lib/odoo",
            "-v", f"{target_dir}/odoo_data:/mnt/extra-addons",
            "-v", f"{target_dir}/odoo_custom_addons:/mnt/custom-addons",
            "--link", f"{db_container_restore}:db",
            safe_tag
        ])

        logging.info(f"Restore-Container running: http://localhost:{port}")

    # -------------------------------------------------------
    # 7. Cleanup temporärer Dateien
    # -------------------------------------------------------
    logging.info("Cleanup temporary data ...")
    shutil.rmtree(tmp_dir)
    if os.path.exists(patched_tar):
        os.remove(patched_tar)

    logging.info("Restore finished.")

# =========================================================
# === CLEANUP FUNCTIONS====================================
# =========================================================

def cleanup_unused_images():
    """Entfernt alte Docker-Image-Dateien, die in keinem Backup mehr referenziert sind."""
    backup_index_file = os.path.join(dir_backup_meta, "backups_index.json")
    images_dir = Path(dir_backup_images)

    if not images_dir.exists():
        logging.info("Kein Image-Verzeichnis vorhanden – nichts zu tun.")
        return

    index_data = load_json(backup_index_file)
    if not isinstance(index_data, list):
        logging.warning("Backup-Index leer oder ungültig – keine Image-Bereinigung.")
        return

    # 🔎 Alle Image-Dateien im Filesystem erfassen
    all_image_files = {str(p) for p in images_dir.glob("odoo_image_*.tar*")}

    # 🔎 Alle im Index referenzierten Pfade
    used_image_files = {b.get("image_path") for b in index_data if b.get("image_path")}
    used_image_files = {str(Path(p)) for p in used_image_files if p not in (None, "unknown")}

    # 🔎 Herausfinden, was gelöscht werden darf
    unused = all_image_files - used_image_files

    if not unused:
        logging.info("Keine verwaisten Docker-Images gefunden.")
        return

    for img in sorted(unused):
        try:
            os.remove(img)
            logging.info(f"Altes Image gelöscht: {img}")
        except Exception as e:
            logging.warning(f"Konnte {img} nicht löschen: {e}")


# =========================================================
# === MAIN ================================================
# =========================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: odoo_backup.py [hourly|daily|manual|restore|reconcile|list]")
        sys.exit(1)

    mode = sys.argv[1].lower()
    ensure_directories()
    index_file = os.path.join(dir_backup_meta, "backups_index.json")

    # =========================================================
    # === LIST MODE ===========================================
    # =========================================================
    if mode == "list":
        data = load_json(index_file)
        if not data:
            print("Keine Einträge im Backup-Index vorhanden.")
            sys.exit(0)
        print(f"\nVerfügbare Backups ({len(data)}):\n")
        for item in data:
            print(f"- {item['timestamp']}  {item['type']:7s}  {item['size_mb']:6.1f} MB  "
                  f"{Path(item['archive_path']).name}")
        sys.exit(0)

    # =========================================================
    # === RECONCILE MODE ======================================
    # =========================================================
    if mode == "reconcile":
        reconcile_all_backups(index_file)
        cleanup_unused_images()
        sys.exit(0)

    # =========================================================
    # === RESTORE MODE ========================================
    # =========================================================
    if mode == "restore":
        if len(sys.argv) < 4:
            print("Usage: odoo_backup.py restore <backup_archive.tar.gz> <target_dir> [port] [no-run]")
            sys.exit(1)

        archive_path = sys.argv[2]
        target_dir   = sys.argv[3]
        port         = int(sys.argv[4]) if len(sys.argv) >= 5 and sys.argv[4].isdigit() else 8079
        start_container = not ("no-run" in sys.argv or "norun" in sys.argv)

        restore_backup(archive_path, target_dir, port=port, start_container=start_container)
        logging.info("Restore completed successfully.")
        sys.exit(0)

    # =========================================================
    # === BACKUP MODES ========================================
    # =========================================================
    backup_type = mode
    logging.info(f"Start Backup ({backup_type}) ...")

    # --- temporäres Arbeitsverzeichnis vorbereiten ---
    tmp_dir = Path("/tmp/odoo_backup_tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(exist_ok=True)

    # --- 1. DB & relevante Verzeichnisse sichern ---
    files = []
    files.append(backup_database(tmp_dir))
    files.extend(backup_directories_func(tmp_dir))

    # --- 2. Docker-Image prüfen/exportieren ---
    # immer export_image_if_new(): Funktion selbst prüft, ob Datei neu ist
    image_id, image_path = export_image_if_new()

    # --- 3. Archiv erstellen ---
    archive_path = create_archive(backup_type, files, image_id, image_path)
    shutil.rmtree(tmp_dir)

    # --- 4. Index aktualisieren ---
    register_backup_in_index(
        archive_path=archive_path,
        backup_type=backup_type,
        image_tag=docker_image_tag,
        image_id=image_id,
        image_path=image_path
    )

    # --- 5. Retention anwenden ---
    if backup_type == "daily":
        cleanup_old_backups(dir_backup_daily, retention_daily)
    elif backup_type == "manual":
        cleanup_old_backups(dir_backup_manual, retention_manual)
    elif backup_type == "hourly":
        cleanup_old_backups(dir_backup_hourly, 2)

    # --- 6. Ungenutzte Docker-Images löschen ---
    cleanup_unused_images()

    # --- 7. Manuelle Backups abgleichen ---
    #    Stellt sicher, dass alle existierenden manual-Backups
    #    korrekt im Index enthalten sind (und verwaiste entfernt werden)
    reconcile_manual_backups(index_file)

    logging.info(f"{backup_type.upper()} Backup successfully completed.")


if __name__ == "__main__":
    main()

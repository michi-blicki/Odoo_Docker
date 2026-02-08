#!/usr/bin/env python3
"""
recreate_odoo_test.py
"""

import argparse
import logging
import subprocess
import sys
import os
from pathlib import Path
import json
import yaml
import shutil
import re
import socket
import time
import requests

# =========================================================
# === SCRIPT CONFIGURATION=================================
# =========================================================

DEFAULT_BACKUP_INDEX_PATH = "/srv/gfs-backup/odoo-prod/meta/backups_index.json"
DEFAULT_BACKUP_ROOT = "/srv/gfs-backup/odoo-prod"
DEFAULT_TEMP_RESTORE_DIR = "/srv/restore/odoo-test/tmp"
DEFAULT_RESTORE_LOG_FILE = "/srv/restore/odoo-test/odoo-test-restore.log"
DEFAULT_STACK_FILE = "/srv/gfs-storage/odoo-test/docker-stack_odoo-test.yml"
DEFAULT_TEST_SQL_FILE = "/home/blicki/docker/odoo-test/odoo-test.sql"
DEFAULT_STACK_NAME = "odoo-test"

ODOO_USER="flectra"
ODOO_GROUP="srv"


# =========================================================
# === GLOBAL HELPERS=======================================
# =========================================================

def run_cmd(cmd, capture_output=False):
    """Shell Command Execution Wrapper"""
    logging.debug(f"Run CMD: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=capture_output, text=True, check=True)
        return result.stdout.strip() if capture_output else None
    except subprocess.CalledProcessError as e:
        logging.error(f"Command Execution Error: {' '.join(cmd)}: {e}")
        sys.exit(1)


def load_json(path):
    """Secure load of JSON file"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading JSON file '{path}': {e}")
        sys.exit(1)


def save_json(path, data):
    """Secure save of JSON file"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving JSON file '{path}': {e}")
        sys.exit(1)


def setup_logging(log_file, verbose=False):
    """Initializing script logging using Python3 logging module"""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info("Logging initialized.")

# =========================================================
# === LIST BACKUP =========================================
# =========================================================
def list_backups(args):
    """List backups from GlusterFC mounted backup directory."""
    logging.info("Loading backup index from GlusterFC backup directory...")

    # 1. Backup-Index laden (direkt von GlusterFC)
    if not os.path.exists(args.backup_index_path):
        logging.error(f"Backup-Index nicht gefunden: {args.backup_index_path}")
        print(f"❌ Fehler: Backup-Index nicht gefunden unter {args.backup_index_path}")
        print(f"Stelle sicher, dass /srv/gfs-backup auf diesem Host gemountet ist.")
        sys.exit(1)

    # 2. JSON laden (direkt von GlusterFC)
    logging.info(f"Lade Backup-Index: {args.backup_index_path}")
    index_data = load_json(args.backup_index_path)

    # 4. Struktur prüfen
    if isinstance(index_data, list):
        backups = index_data
    elif isinstance(index_data, dict) and "backups" in index_data:
        backups = index_data["backups"]
    else:
        logging.error("Unerwartetes Format in backups_index.json – erwartete Liste von Objekten!")
        print("\n❌ Fehler: Unexpected JSON format in backups_index.json")
        sys.exit(1)

    # 5. Prüfen auf Einträge
    if not backups:
        logging.warning("Keine Backups im Index gefunden.")
        print("\nKeine Backups verfügbar.")
        return

    # 6. Ausgabeformat vorbereiten
    print("\nVerfügbare Backups:\n")
    header = f"{'Nr.':<3} {'Timestamp':<28} {'Type':<10} {'Size(MB)':>9}  {'Image Tag':<24} {'Image SHA256':<16}"
    print(header)
    print("-" * len(header))

    # 7. Daten ausgeben
    for i, b in enumerate(backups, start=1):
        ts = b.get("timestamp", "n/a")
        bt = b.get("type", "n/a")
        size = b.get("size_mb", "n/a")
        tag = b.get("image_tag", "")[:24]
        img_sha = b.get("image_sha256", "")[:16]
        print(f"{i:<3} {ts:<28} {bt:<10} {size:>9}  {tag:<24} {img_sha:<16}")

    print(f"\nTotal: {len(backups)} Backups found.\n")


# =========================================================
# === PREPARE RESTORE =====================================
# =========================================================
def prepare_restore_environment(args, restore_dir: str) -> dict:
    """
    Bereitet das lokale Testsystem auf den Restore vor:
    - Docker Stack Datei auswerten
    - Test-Services stoppen und entfernen
    - Backup-Archiv entpacken
    - meta.json laden
    - Pfade von odoo-prod -> odoo-test umschreiben
    - GlusterFC-Verzeichnisse leeren (odoo_data, custom_addons, ...)
    """
    logging.info("Starte Schritt 4: Vorbereitung der Restore-Umgebung ...")

    # 4.1 Docker Stack Datei laden
    stack_path = Path(args.stack_file or DEFAULT_STACK_FILE)
    if not stack_path.exists():
        logging.error(f"{stack_path} nicht gefunden – Abbruch.")
        sys.exit(1)
    with open(stack_path, "r") as f:
        stack_data = yaml.safe_load(f)
    services = list(stack_data.get("services", {}).keys())
    volumes = list(stack_data.get("volumes", {}).keys())
    logging.info(f"Docker-Services: {services}")
    logging.info(f"Docker-Volumes: {volumes}")

    # 4.2 Docker Stack entfernen (stoppe alle Services und entferne Stack)
    print("🛑 Stoppe und entferne Docker Stack 'odoo-test' ...")
    remove_stack_cmd = ["docker", "stack", "rm", args.stack_name]
    try:
        run_cmd(remove_stack_cmd)
        # Warte kurz, damit Services vollständig heruntergefahren sind
        time.sleep(3)
    except SystemExit:
        logging.warning("Stack war möglicherweise nicht aktiv oder konnte nicht entfernt werden.")
    
    logging.info(f"Stack '{args.stack_name}' entfernt.")

    # 4.3 Backup-Archiv entpacken (ausschließlich echte Backup-Dateien – Docker-Images ignorieren)
    backup_archives = [
        f for f in os.listdir(restore_dir)
        if f.endswith(".tar.gz") and not f.startswith("odoo_image_sha256_")
    ]

    if len(backup_archives) == 0:
        logging.error(
            f"Kein gültiges Backup-Archiv in {restore_dir} gefunden. "
            "Erwartet Dateien wie 'backup_hourly_XX.tar.gz', aber nur Docker-Image-Tars vorhanden."
        )
        sys.exit(1)

    if len(backup_archives) > 1:
        logging.warning(
            f"Mehrere Backup-Archive im Ordner {restore_dir} gefunden: {backup_archives}. "
            "Verwende das zuletzt geänderte."
        )
        archive_files_stats = [(f, os.path.getmtime(os.path.join(restore_dir, f))) for f in backup_archives]
        archive_file = sorted(archive_files_stats, key=lambda x: x[1], reverse=True)[0][0]
    else:
        archive_file = backup_archives[0]

    archive_path = os.path.join(restore_dir, archive_file)
    print(f"📦 Entpacke Backup-Archiv {archive_file} ...")
    run_cmd(["tar", "xf", archive_path, "-C", restore_dir])

    # 4.4 meta.json laden
    meta_path = os.path.join(restore_dir, "meta.json")
    if not os.path.exists(meta_path):
        logging.error("meta.json im entpackten Archiv nicht gefunden.")
        sys.exit(1)
    meta = load_json(meta_path)
    logging.info(f"Meta-Datei geladen: {meta_path}")

    # 4.5 Verzeichnis‑Pfade von odoo‑prod zu odoo‑test ändern (neue Struktur unterstützen)
    new_dirs = []

    dirs_meta = meta.get("directories", [])
    if dirs_meta and isinstance(dirs_meta[0], dict):
        for d in dirs_meta:
            src_dir = d.get("directory")
            file_name = d.get("file")
            if not src_dir:
                continue
            new_entry = {
                "directory": src_dir.replace("odoo-prod", "odoo-test"),
                "file": file_name
            }
            new_dirs.append(new_entry)
    else:
        # Legacy: einfache String-Liste
        for d in dirs_meta:
            new_dirs.append(d.replace("odoo-prod", "odoo-test"))

    meta["directories"] = new_dirs
    logging.info(f"Angepasste Verzeichnisse: {new_dirs}")

    # 4.6–4.8: Verzeichnisse leeren (neue Struktur + sudo‑Rechte)
    for entry in new_dirs:
        target_dir = entry["directory"]

        if not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)

        logging.info(f"Leere {target_dir} (mit sudo) …")
        print(f"🧹 Leere {target_dir} (Host, sudo)…")

        try:
            run_cmd(["sudo", "rm", "-rf", os.path.join(target_dir, "*")])
            logging.info(f"Inhalt von {target_dir} erfolgreich gelöscht (sudo).")
            print(f"✅ Alle Inhalte von {target_dir} gelöscht (mit sudo).")
        except SystemExit:
            logging.error(f"Fehler beim Löschen von {target_dir} (sudo).")
            print(f"❌ Fehler beim Löschen von {target_dir} (mit sudo).")
            return
        except Exception as e:
            logging.warning(f"Konnte {target_dir} nicht löschen: {e}")
            print(f"⚠️ Konnte {target_dir} nicht löschen: {e}")

    # 4.9: GlusterFC-Verzeichnisse aus Stack-Definition leeren
    # Extrahiere alle device-Pfade aus den volumes
    for vol_name, vol_config in stack_data.get("volumes", {}).items():
        driver_opts = vol_config.get("driver_opts", {})
        if driver_opts.get("type") == "none" and "device" in driver_opts:
            device_path = driver_opts["device"]
            if device_path and os.path.exists(device_path):
                logging.info(f"Leere GlusterFC-Pfad {device_path} …")
                print(f"🧹 Leere {device_path} (GlusterFC, sudo) …")

                try:
                    run_cmd(["sudo", "rm", "-rf", os.path.join(device_path, "*")])
                    logging.info(f"Inhalt von {device_path} erfolgreich gelöscht (sudo).")
                    print(f"✅ Alle Inhalte von {device_path} gelöscht (mit sudo).")
                except SystemExit:
                    logging.error(f"Fehler beim Löschen von {device_path} (mit sudo).")
                    print(f"❌ Fehler beim Löschen von {device_path} (mit sudo).")
                    return
            else:
                logging.debug(f"Volume-Pfad {device_path} nicht gefunden oder keine device-Bindung.")

    logging.info("Restore‑Umgebung erfolgreich vorbereitet.")
    return meta

# =========================================================
# === RECREATE BACKUP =====================================
# =========================================================
def get_local_image_id(image_tag: str) -> str | None:
    """Gibt die vollständige Image-ID (sha256:...) des lokal vorhandenen Docker-Images zurück, falls vorhanden."""
    try:
        result = subprocess.run(
            ["docker", "images", "--no-trunc", "--quiet", image_tag],
            capture_output=True, text=True, check=True
        )
        image_id = result.stdout.strip()
        if image_id:
            logging.debug(f"Lokales Docker-Image gefunden: {image_tag} => {image_id}")
            return f"sha256:{image_id.replace('sha256:', '')}"
    except subprocess.CalledProcessError:
        pass
    return None


def recreate_backup(args):
    """Recreate selected Odoo backup on local test system."""
    ts = args.timestamp
    logging.info(f"Start restore process for backup with timestamp: {ts}")

    # === Phase A: Backupdaten vorbereiten ===
    os.makedirs(args.temp_restore_dir, exist_ok=True)
    restore_dir = os.path.join(args.temp_restore_dir, f"restore_{ts}")
    os.makedirs(restore_dir, exist_ok=True)

    # Index laden und Backup-Eintrag finden (from GlusterFC, not temp dir)
    index_data = load_json(args.backup_index_path)
    backups = index_data if isinstance(index_data, list) else index_data.get("backups", [])
    selected = next((b for b in backups if b.get("timestamp") == ts), None)

    if not selected:
        logging.error(f"Backup mit Timestamp {ts} nicht im Index gefunden.")
        print(f"❌ Backup {ts} nicht gefunden.")
        return

    archive_remote = selected.get("archive_path")
    image_remote   = selected.get("image_path")
    image_tag      = selected.get("image_tag")
    image_id       = selected.get("image_id")

    print(f"\n=================================================")
    print(f"🚀 Restore Backup: {ts}")
    print(f"=================================================")
    print(f"Type:   {selected.get('type')}")
    print(f"Size:   {selected.get('size_mb')} MB")
    print(f"Image:  {image_tag}")
    print(f"SHA256: {image_id}")
    print(f"Archive: {archive_remote}\n")

    # Backup-Archiv vom GlusterFC kopieren (lokal verfügbar)
    local_archive_path = os.path.join(restore_dir, Path(archive_remote).name)
    if not os.path.exists(archive_remote):
        logging.error(f"Backup-Archiv nicht gefunden: {archive_remote}")
        print(f"❌ Fehler: Backup-Archiv '{archive_remote}' nicht gefunden.")
        print(f"Stelle sicher, dass /srv/gfs-backup auf diesem Host gemountet ist.")
        sys.exit(1)
    
    logging.info(f"Copy Backup-Archiv from {archive_remote} to {local_archive_path}")
    shutil.copy2(archive_remote, local_archive_path)
    logging.info(f"Backup-Archiv erfolgreich kopiert.")

    # === Phase B: Vorbereitung der Restore-Umgebung (Schritt 4) ===
    meta = prepare_restore_environment(args, restore_dir)

    # === Phase C: Docker-Image prüfen (bereits vorhanden auf allen Nodes) ===
    # Das Image blicki/odoo:18.0-PROD sollte bereits auf allen Swarm-Nodes vorhanden sein
    if image_tag:
        local_id = get_local_image_id(image_tag)
        desired_id = selected.get("image_id")

        if local_id == desired_id:
            logging.info(f"Local docker image {image_tag} matches required image. Using local image.")
            print(f"✅ Image {image_tag} ({desired_id[:20]}...) bereits vorhanden. Nutze lokales Image.")
        else:
            logging.warning(
                f"Image {image_tag} not found locally or does not match requirement (local: {local_id}, required: {desired_id})."
            )
            print(f"⚠️  Image {image_tag} nicht vorhanden oder stimmt nicht überein.")
            print(f"Erwartet: {desired_id}")
            print(f"Vorhanden: {local_id if local_id else 'nicht vorhanden'}")
            print(f"ℹ️  Stelle sicher, dass das Image auf diesem Swarm-Node vorhanden ist.")
    else:
        logging.warning("No image_tag in Backup-entry found!")

    # === Phase D: Abschluss Zusammenfassung ===
    print("\n✅ Restore-Vorbereitung (Schritt 4+5) abgeschlossen.")
    print(f"Entpackte Dateien unter: {restore_dir}\n")
    logging.info(f"Restore preparation for {ts} successfully finished.")

    # === Phase E: PostgreSQL-Datenbank wiederherstellen (Schritt 6) ===
    restore_database(args, meta, restore_dir)

    # === Phase F: Wiederherstellen der Odoo-Verzeichnisse (Schritt 7) ===
    restore_directories(args, meta, restore_dir)


# =========================================================
# === RESTORE DATABASE (SCHRITT 6) ========================
# =========================================================
def restore_database(args, meta, restore_dir):
    """
    Schritt 6: Wiederherstellen der PostgreSQL-Datenbank (odoo-test-db)
    ----------------------------------------------------------
    6.1 Stack deployen (nur DB Service)
    6.2 Warten bis DB Service ready ist (max. 30 Sek.)
    6.3 Datenbank-Dump (odoo-db.sql.gz) importieren
    6.4 odoo-test.sql ausführen (Kennzeichnung der Testinstanz)
    """

    logging.info("=== Schritt 6: Datenbank-Restore auf odoo-test starten ===")
    db_service = "db"  # Service-Name aus docker-stack_odoo-test.yml
    stack_file = args.stack_file or DEFAULT_STACK_FILE
    stack_name = args.stack_name
    
    # Get database config from meta (docker_backup.py stores it as 'database')
    db_config = meta.get("database", {})
    if not db_config:
        logging.error("Keine Datenbank-Konfiguration in meta.json gefunden.")
        print("❌ Fehler: Keine Datenbank-Informationen im Backup.")
        return
    
    db_user = db_config.get("user", "odoo")
    db_name = db_config.get("name", "odoo")

    # --- 6.1 Docker Stack deployen ---
    print(f"🐘 Deploye Docker Stack '{stack_name}' (nur DB Service für erste Phase) ...")
    logging.info(f"Deploye Stack {stack_name} mit {stack_file}")
    
    # Beim ersten Deploy wird die Datenbank initialisiert
    deploy_cmd = ["docker", "stack", "deploy", "-c", stack_file, stack_name]
    run_cmd(deploy_cmd)

    # --- 6.2 Auf 'healthy' warten ---
    print("⏳ Warte auf Datenbank-Container (max. 20 Sekunden) ...")
    healthy = False
    for i in range(20):
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", db_service],
                capture_output=True, text=True, check=True
            )
            status = result.stdout.strip()
            logging.debug(f"Health-Status {db_service}: {status}")
            if status == "healthy":
                healthy = True
                break
        except subprocess.CalledProcessError:
            logging.debug(f"Container {db_service} noch nicht gestartet.")
        time.sleep(1)

    if not healthy:
        logging.error(f"Container {db_service} wurde nicht healthy innerhalb von 20 Sekunden.")
        print("❌ PostgreSQL ist nicht bereit – Restore abgebrochen.")
        return
    print("✅ PostgreSQL ist bereit.")

    # --- 6.3 Datenbank-Dump einspielen ---
    # docker_backup.py creates dump as {stack_name}-db.sql.gz (e.g., odoo-prod-db.sql.gz)
    dump_gz = os.path.join(restore_dir, f"odoo-prod-db.sql.gz")
    if not os.path.isfile(dump_gz):
        logging.error(f"Datenbank-Dump {dump_gz} nicht gefunden.")
        print(f"❌ Dump-Datei {dump_gz} fehlt, Restore kann nicht fortgesetzt werden.")
        print(f"Stelle sicher, dass das Backup korrekt entpackt wurde.")
        return

    print(f"📥 Importiere Datenbank-Dump '{os.path.basename(dump_gz)}' ...")

    # Datenbank neu erstellen (sauberer Zustand)
    print(f"🧹 Lösche ggf. alte Datenbank '{db_name}' und erstelle neu …")

    drop_query = f"DROP DATABASE IF EXISTS {db_name};"
    create_query = f"CREATE DATABASE {db_name};"

    for query in (drop_query, create_query):
        run_cmd([
            "docker", "exec", "-i", db_container,
            "psql", "-U", db_user, "-d", "postgres",
            "-c", query
        ])

    # Schritt: Datenbank-Dump importieren (silent, mit Log)
    dump_log = os.path.join(args.temp_restore_dir, "psql_import.log")
    gunzip_proc = subprocess.Popen(["gunzip", "-c", dump_gz], stdout=subprocess.PIPE)
    import_psql = ["docker", "exec", "-i", db_container, "psql", "-U", db_user, "-q", "-d", db_name]
    with open(dump_log, "w") as logf:
        subprocess.run(import_psql, stdin=gunzip_proc.stdout, stdout=logf, stderr=logf, check=True)
    gunzip_proc.wait()

    print(f"✅ Datenbank-Dump erfolgreich importiert. (Details siehe {dump_log})")
    logging.info(f"Dump erfolgreich eingespielt (Log unter {dump_log}).")

    # --- 6.4 odoo-test.sql ausführen ---
    test_sql = args.test_sql_file
    if os.path.exists(test_sql):
        print("🧩 Importiere odoo-test.sql (Testumgebung-Kennzeichnung) ...")
        with open(test_sql, "rb") as sql_file:
            subprocess.run(
                ["docker", "exec", "-i", db_service, "psql", "-U", db_user, "-d", db_name],
                stdin=sql_file, check=True
            )
        logging.info(f"{test_sql} erfolgreich importiert.")
        print("✅ odoo-test.sql erfolgreich eingespielt.")
    else:
        logging.warning(f"Testdatei {test_sql} nicht gefunden – übersprungen.")
        print(f"⚠️ Testdatei {test_sql} nicht gefunden – übersprungen.")

    print("\n✅ PostgreSQL-Restore abgeschlossen.\n")
    logging.info("Schritt 6 erfolgreich abgeschlossen.")


# =========================================================
# === RESTORE DIRECTORIES (SCHRITT 7) =====================
# =========================================================
def restore_directories(args, meta, restore_dir):
    """
    Schritt 7: Wiederherstellen der Odoo‑Verzeichnisse
    --------------------------------------------------
    Entpackt die TAR‑Archive gemäß neuer meta['directories']‑Struktur:
    [
      {"directory": "/srv/docker/odoo-prod/odoo_local_share", "file": "odoo_local_share.tar.gz"},
      ...
    ]
    Entpackung erfolgt mit sudo auf dem Hostsystem (nicht im Container).
    Danach werden Ownership & Gruppe rekursiv auf ODOO_USER:ODOO_GROUP gesetzt.
    """
    logging.info("=== Schritt 7: Starte Wiederherstellung der Odoo‑Verzeichnisse (Host‑Seite, mit sudo) ===")

    dirs_mappings = meta.get("directories", [])
    if not dirs_mappings:
        logging.error("meta.json enthält keine Verzeichnisse.")
        print("❌ Keine Verzeichnisse oder Dateien in meta.json gefunden.")
        return

    for mapping in dirs_mappings:
        target_dir = mapping.get("directory")
        archive_name = mapping.get("file")

        if not target_dir or not archive_name:
            logging.warning(f"Ungültiger Eintrag in meta['directories']: {mapping}")
            continue

        # Skip odoo_custom_addons – entwickelt separat für odoo-test
        if "odoo_custom_addons" in target_dir:
            logging.info(f"Überspringe {archive_name} (odoo_custom_addons wird separat entwickelt)")
            print(f"⏭️  Überspringe {archive_name} (odoo_custom_addons wird separat auf odoo-test entwickelt)")
            continue

        # PROD → TEST‑Pfad ersetzen oder unter restore_dir legen
        target_dir = target_dir.replace("odoo-prod", "odoo-test")
        archive_path = os.path.join(restore_dir, archive_name)

        if not os.path.isfile(archive_path):
            logging.warning(f"Archiv {archive_path} nicht gefunden – übersprungen.")
            print(f"⚠️ Archiv {archive_path} fehlt, überspringe …")
            continue

        Path(target_dir).mkdir(parents=True, exist_ok=True)
        print(f"📦 Entpacke {archive_name} → {target_dir} (als root) …")
        logging.info(f"Entpacke {archive_name} nach {target_dir} mit sudo")

        try:
            run_cmd(["sudo", "tar", "xzf", archive_path, "-C", target_dir])
            print(f"✅ {archive_name} erfolgreich entpackt (Host, sudo).")
        except SystemExit:
            logging.error(f"Fehler beim Entpacken von {archive_name} (sudo).")
            print(f"❌ Fehler beim Entpacken von {archive_name}.")
            return

        # 🔹 Nach dem Entpacken Ownership auf ODOO_USER:ODOO_GROUP ändern
        print(f"🔧 Setze Benutzer/Gruppenrechte auf {ODOO_USER}:{ODOO_GROUP} für {target_dir} …")
        logging.info(f"Setze Ownership → {ODOO_USER}:{ODOO_GROUP} rekursiv auf {target_dir}")

        try:
            run_cmd(["sudo", "chown", "-R", f"{ODOO_USER}:{ODOO_GROUP}", target_dir])
            logging.info(f"Ownership erfolgreich gesetzt auf {target_dir} → {ODOO_USER}:{ODOO_GROUP}.")
            print(f"✅ Ownership gesetzt auf {target_dir} ({ODOO_USER}:{ODOO_GROUP}).")
        except SystemExit:
            logging.error(f"Fehler beim chown von {target_dir}.")
            print(f"❌ Fehler beim Setzen der Ownership für {target_dir}.")
            return

    logging.info("Alle Verzeichnisse erfolgreich auf Host wiederhergestellt (mit sudo).")
    print("\n✅ Alle Odoo‑Verzeichnisse wiederhergestellt und Ownership gesetzt (sudo).\n")



# =========================================================
# === START CONTAINERS (SCHRITT 8) ========================
# =========================================================
def start_containers(args):
    """
    Schritt 8: Startet alle Container des odoo-test Stacks gemäß docker-compose.yml.
    -------------------------------------------------------
    - Startet interaktiv mit 'docker compose up -d'
    - Gibt Liste der gestarteten Services aus
    - Kein Health-Check (folgt in Schritt 9)
    """
    logging.info("=== Schritt 8: Starte Odoo-Test Container ===")

    compose_file = args.docker_compose_file
    if not os.path.exists(compose_file):
        logging.error(f"docker-compose.yml nicht gefunden unter {compose_file}")
        print(f"❌ Compose-File {compose_file} nicht gefunden – Abbruch.")
        return

    print(f"🚀 Starte Odoo-Test Umgebung mit Compose-File: {compose_file}")
    logging.info(f"Starte Container basierend auf {compose_file}")

    try:
        run_cmd(["docker", "compose", "-f", compose_file, "up", "-d"])
    except SystemExit:
        # run_cmd ruft sys.exit(), also Fehlerfall explizit behandeln
        logging.error("Fehler beim Starten der Docker-Container.")
        print("❌ Fehler beim Start der Docker-Container – siehe Logfile.")
        return

    # Liste der gestarteten Container anzeigen
    print("\n🧩 Aktive Container:")
    cmd = ["docker", "ps", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(result.stdout)
        logging.info("Container erfolgreich gestartet:\n" + result.stdout)
    except subprocess.CalledProcessError as e:
        logging.warning(f"Konnte Containerliste nicht ausgeben: {e}")

    print("✅ Alle Odoo-Test Container erfolgreich gestartet.\n")
    logging.info("Schritt 8 abgeschlossen – Container laufen.")


# =========================================================
# === HEALTH CHECK (SCHRITT 9) ============================
# =========================================================
def check_health(args):
    """
    Schritt 9: Überprüft Zustand des gesamten odoo-test Stacks
    ----------------------------------------------------------
    A) Prüft dass alle Container 'Up' sind
    B) Prüft, dass die odoo-test-App Ports 8069/tcp und 8072/tcp offen sind
    C) Fragt Odoo HTTP-Status (z. B. /web/health oder /) ab
    """
    print("\n🔍 Starte Systemprüfung (Schritt 9: Health‑Check) …")
    logging.info("=== Schritt 9: Health‑Check gestartet ===")

    # --- A) Docker-Container prüfen ---
    print("🧩 Prüfe laufende Docker‑Container …")
    cmd = ["docker", "ps", "--format", "{{.Names}}:{{.Status}}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        logging.error("Fehler beim Lesen der Docker‑Containerliste.")
        print("❌ Docker‑Containerliste konnte nicht gelesen werden.")
        return False

    lines = result.stdout.strip().splitlines()
    if not lines:
        print("❌ Keine laufenden Container gefunden!")
        logging.error("Keine Container aktiv.")
        return False

    all_up = True
    odoo_container = None
    for line in lines:
        name, status = line.split(":", 1)
        if "odoo-test" in name:
            print(f"   ↳ {name:25s} : {status}")
            if "Up" not in status:
                all_up = False
        if re.search(r"odoo-test(-app|-odoo)?$", name):
            odoo_container = name
    if not all_up:
        print("❌ Nicht alle Container sind im Status 'Up'.")
        logging.warning("Ein oder mehrere Container sind nicht 'Up'.")
        return False
    print("✅ Alle Container sind aktiv und 'Up'.")

    # --- B) Prüf Ports 8069 & 8072 (Docker oder lokale sockets) ---
    print(f"🔌 Prüfe Ports 8069 und 8072 …")
    ports_ok = True
    for port in (8069, 8072):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        try:
            sock.connect(("127.0.0.1", port))
            print(f"✅ Port {port} offen.")
        except Exception as e:
            ports_ok = False
            print(f"❌ Port {port} nicht erreichbar ({e}).")
            logging.warning(f"Port {port} nicht offen: {e}")
        finally:
            sock.close()
    if not ports_ok:
        print("⚠️  Ein oder mehrere Ports nicht erreichbar – bitte Compose prüfen.")
        return False

    # --- C) Odoo‑Health prüfen (innerhalb des Containers) ---
    print("🌐 Prüfe Odoo‑HTTP‑Erreichbarkeit im Container …")
    ok = False
    try:
        output = run_cmd([
            "docker", "exec", "odoo-test-app",
            "curl", "-sf", "http://localhost:8069/web/health"
        ], capture_output=True)
        if "ok" in output.lower() or "odoo" in output.lower():
            ok = True
            print("✅ Odoo antwortet korrekt innerhalb des Containers (/web/health).")
            logging.info("Odoo Health‑Check im Container erfolgreich.")
        else:
            print("⚠️ Odoo antwortet, aber Inhalt unerwartet.")
            logging.debug(f"Antwort: {output[:160]}")
    except SystemExit:
        pass
    except Exception as e:
        logging.warning(f"Odoo Health‑Prüfung im Container fehlgeschlagen: {e}")
        print(f"❌ Odoo Health‑Prüfung im Container fehlgeschlagen: {e}")

    if not ok:
        print("❌ Odoo‑Webserver nicht erreichbar oder kein gültiger Status.")
        logging.error("Odoo Web‑Health‑Check fehlgeschlagen.")
        return False

    print("\n✅ Health‑Check erfolgreich – System reagiert wie erwartet.\n")
    logging.info("Health‑Check abgeschlossen – System OK.")
    return True


# =========================================================
# === MENU MODE ===========================================
# =========================================================
def menu_mode(args):
    """Interactive menu to guide through backup recreation process."""
    logging.info("Menu mode started.")

    # 1. Backup-Index laden direkt von GlusterFC
    if not os.path.exists(args.backup_index_path):
        logging.error(f"Backup-Index nicht gefunden: {args.backup_index_path}")
        print(f"❌ Fehler: Backup-Index nicht gefunden unter {args.backup_index_path}")
        print(f"Stelle sicher, dass /srv/gfs-backup auf diesem Host gemountet ist.")
        sys.exit(1)

    logging.info(f"Lade Backup-Index: {args.backup_index_path}")
    index_data = load_json(args.backup_index_path)
    if isinstance(index_data, list):
        backups = index_data
    elif isinstance(index_data, dict) and "backups" in index_data:
        backups = index_data["backups"]
    else:
        print("❌ Unexpected JSON format in backups_index.json")
        sys.exit(1)

    if not backups:
        print("No backups found in index.")
        return

    # 4. Liste anzeigen
    print("\n===========================================================")
    print("     💾  Available Odoo Backups (from PROD‑System)")
    print("===========================================================\n")
    for i, b in enumerate(backups, start=1):
        print(f"{i:>2}. {b.get('timestamp','n/a')}  [{b.get('type','n/a')}]  "
              f"{b.get('size_mb','n/a')} MB  {b.get('image_tag','')}")
    print()

    # 5. Auswahl einlesen
    while True:
        user_input = input("🔸 Specify backup image ID (or [q] to quit): ").strip()
        if user_input.lower() in ("q", "quit", "exit"):
            print("Exit by User.")
            logging.info("User exited menu mode.")
            return
        if not user_input.isdigit():
            print("Please specify valid id.")
            continue

        index = int(user_input)
        if not (1 <= index <= len(backups)):
            print(f"Please specify ID between 1 and {len(backups)}.")
            continue
        break

    # 6. Gewähltes Backup extrahieren
    selected = backups[index - 1]
    ts = selected.get("timestamp")
    size = selected.get("size_mb")
    print(f"\n➡️  Backup selected: {ts} ({size} MB)")

    # 7. Bestätigung holen
    confirm = input("\nStart recreation? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes", "j", "ja"):
        print("Canceled – no recreation started.")
        logging.info("Restore aborted by user.")
        return

    # 8. recreate_backup mit Timestamp aufrufen
    logging.info(f"User selected backup {ts} – calling recreate_backup().")
    args.timestamp = ts
    recreate_backup(args)

    # 9. Optional Containerstart abfragen (Schritt 7 später implementiert)
    start_confirm = input("\nStart containers after restore? [y/N]: ").strip().lower()
    if start_confirm in ("y", "yes", "j", "ja"):
        start_containers(args)
        check_health(args)
    else:
        print("Containers start skipped.")
        logging.info("Container start skipped.")


# =========================================================
# === MAIN ================================================
# =========================================================
def main():
    parser = argparse.ArgumentParser(
        description="Recreate odoo-test from remotely located odoo-prod"
    )

    # Standardparameter mit Defaults
    parser.add_argument("--backup-index-path", default=DEFAULT_BACKUP_INDEX_PATH,
                        help=f"Path to backups_index.json on GlusterFC (Default: {DEFAULT_BACKUP_INDEX_PATH})")
    parser.add_argument("--backup-root", default=DEFAULT_BACKUP_ROOT,
                        help=f"Root path to backups on GlusterFC (Default: {DEFAULT_BACKUP_ROOT})")
    parser.add_argument("--temp-restore-dir", default=DEFAULT_TEMP_RESTORE_DIR,
                        help=f"Local temporary Restore directory (Default: {DEFAULT_TEMP_RESTORE_DIR})")
    parser.add_argument("--restore-log-file", default=DEFAULT_RESTORE_LOG_FILE,
                        help=f"Path to Restore log file (Default: {DEFAULT_RESTORE_LOG_FILE})")
    parser.add_argument("--stack-file", default=DEFAULT_STACK_FILE,
                        help=f"Path to docker-stack_odoo-test.yml (Default: {DEFAULT_STACK_FILE})")
    parser.add_argument("--stack-name", default=DEFAULT_STACK_NAME,
                        help=f"Docker Swarm stack name (Default: {DEFAULT_STACK_NAME})")
    parser.add_argument("--test-sql-file", default=DEFAULT_TEST_SQL_FILE,
                        help=f"Path to odoo-test.sql file (Default: {DEFAULT_TEST_SQL_FILE})")

    parser.add_argument("--verbose", action="store_true", help="Aktiviert Debug-Logging")

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Befehle")

    parser_list = subparsers.add_parser("list", help="List available backups")
    parser_backup = subparsers.add_parser("backup", help="Start restore of given backup timestamp")
    parser_backup.add_argument("timestamp", help="Backup timestamp")
    parser_backup.add_argument("--start-containers", action="store_true", help="Start containers automatically after successful restore.")
    parser_health = subparsers.add_parser("health", help="Run health check of odoo-test stack")

    args = parser.parse_args()
    setup_logging(args.restore_log_file, args.verbose)

    # Hauptsteuerung
    if not args.command:
        menu_mode(args)
    elif args.command == "list":
        list_backups(args)
    elif args.command == "backup":
        recreate_backup(args)
        if args.start_containers:
            start_containers(args)
            check_health(args)
    elif args.command == "help":
        parser.print_help()
    elif args.command == "health":
        check_health(args)
    else:
        parser.print_help()        


if __name__ == "__main__":
    main()
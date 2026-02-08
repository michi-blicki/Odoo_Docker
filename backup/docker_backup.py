#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generic Docker Stack Backup Script with section banners
and informational STDOUT messages.
Version 3.3 — meta.json now includes rich 'database' and 'directories' structures.
"""

import os
import sys
import tarfile
import shutil
import hashlib
import logging
import subprocess
import datetime
import yaml
import argparse
import json
from pathlib import Path

# =========================================================
# === ARGUMENT PARSING =====================================
# =========================================================

def parse_args():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_cfg = os.path.join(script_dir, "docker_backup.yml")
    parser = argparse.ArgumentParser(
        description="Generic Docker Stack Backup Script",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--stack", required=True, help="Stack name defined in YAML")
    parser.add_argument("--mode", required=True,
                        choices=["hourly", "daily", "manual", "reconcile", "list", "delete"],
                        help="Backup mode")
    parser.add_argument("--config_file", default=default_cfg,
                        help="Path to docker_backup.yml")
    parser.add_argument("--backup_base_dir", default="/srv/gfs-backup",
                        help="Root backup directory")
    return parser.parse_args()

# =========================================================
# === LOGGING & PRINT HELPER ===============================
# =========================================================

def log_echo(message, level="info"):
    """
    Logs line into configured logging handlers.
    StreamHandler already mirrors to STDOUT.
    """
    clean_msg = ''.join(ch for ch in str(message) if ch.isprintable())
    if level.lower() == "debug":
        logging.debug(clean_msg)
    elif level.lower() in ("warn", "warning"):
        logging.warning(clean_msg)
    elif level.lower() == "error":
        logging.error(clean_msg)
    else:
        logging.info(clean_msg)

def banner(msg):
    line = "═" * len(msg)
    log_echo(line)
    log_echo(msg)
    log_echo(line)

def step(msg):
    log_echo(f"➡️  {msg}")

def success(msg):
    log_echo(f"✅  {msg}")

def run_cmd(cmd, capture_output=False):
    logging.debug(f"Run: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=capture_output,
                                text=True, check=True)
        return result.stdout.strip() if capture_output else None
    except subprocess.CalledProcessError as e:
        log_echo(f"❌ Command failed: {e}")
        sys.exit(1)

def compute_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return []

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

def ensure_dir(p):
    Path(p).mkdir(parents=True, exist_ok=True)

def load_stack_config(stack_name, cfg_path):
    if not os.path.exists(cfg_path):
        log_echo(f"❌  Configuration file not found: {cfg_path}")
        sys.exit(1)
    with open(cfg_path, "r") as f:
        data = yaml.safe_load(f)
    stacks = data.get("stacks", {})
    if stack_name not in stacks:
        log_echo(f"❌  Stack '{stack_name}' not found in configuration.")
        sys.exit(1)
    return stacks[stack_name]

# =========================================================
# === BACKUP FUNCTIONS =====================================
# =========================================================

def backup_database(stack_name, cfg, target_dir):
    db = cfg["db"]
    if "password" not in db and "passwor" in db:
        db["password"] = db["passwor"]  # typo fallback

    dump_path = os.path.join(target_dir, f"{stack_name}-db.sql.gz")
    step("Creating database dump ...")

    if db["type"] == "postgres":
        cmd = [
            "docker", "run", "--rm",
            "--network", f"{stack_name}_{cfg['network']}",
            "-e", f"PGPASSWORD={db['password']}",
            db["image"],
            "pg_dump", "-h", db["host"], "-U", db["user"], db["name"]
        ]
    elif db["type"] == "mysql":
        cmd = [
            "docker", "run", "--rm",
            "--network", f"{stack_name}_{cfg['network']}",
            "-e", f"MYSQL_PWD={db['password']}",
            db["image"],
            "mysqldump", "-h", db["host"], "-u", db["user"], db["name"]
        ]
    else:
        log_echo(f"⚠️   Unsupported DB type: {db['type']}")
        return None

    with open(dump_path, "wb") as f_out:
        p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        p2 = subprocess.Popen(["gzip", "-9"], stdin=p1.stdout, stdout=f_out)
        p1.stdout.close()
        p2.communicate()

    success(f"Database dump finished → {dump_path}")
    return {
        "type": db["type"],
        "user": db["user"],
        "name": db["name"],
        "image": db["image"],
        "file": os.path.basename(dump_path),
        "path": dump_path
    }

def backup_directories(cfg, tmp_dir):
    results = []
    dirs = cfg.get("backup_dirs", [])
    if not dirs:
        log_echo("ℹ️   No directories configured for backup.")
        return results
    step("Archiving configured directories ...")
    for src in dirs:
        if not os.path.exists(src):
            log_echo(f"⚠️   Directory missing, skipping: {src}")
            continue
        name = Path(src).name
        dst = os.path.join(tmp_dir, f"{name}.tar.gz")
        with tarfile.open(dst, "w:gz") as tar:
            tar.add(src, arcname=name)
        log_echo(f"   →  {src}")
        results.append({"directory": src, "file": os.path.basename(dst), "path": dst})
    success("Directory archive(s) created")
    return results

def export_images(cfg, image_dir):
    images = cfg.get("images", [])
    if not images:
        log_echo("ℹ️   No Docker images configured — skipping image export.")
        return (None, None)
    step("Exporting Docker image(s) ...")
    for tag in images:
        img_id = run_cmd(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
            capture_output=True
        )
        image_filename = f"{tag.replace('/', '_').replace(':', '_')}_{img_id.replace(':', '_')}.tar"
        image_tar = os.path.join(image_dir, image_filename)
        image_gz = f"{image_tar}.gz"

        if os.path.exists(image_gz):
            log_echo(f"   →  Reusing existing image: {image_gz}")
            return (img_id, image_gz)

        run_cmd(["docker", "save", "-o", image_tar, tag])
        run_cmd(["gzip", "-9", image_tar])
        shutil.move(image_tar + ".gz", image_gz)
        size = Path(image_gz).stat().st_size / 1024 / 1024
        sha = compute_sha256(image_gz)
        log_echo(f"   →  {tag} exported ({size:.1f} MB, {sha[:12]}...)")
        success("Docker image export finished")
        return (img_id, image_gz)
    return (None, None)

def create_archive(stack, mode, db_info, dirs_info, image_id, image_path, image_tag, base_dirs):
    step("Creating combined backup archive ...")
    now = datetime.datetime.now()
    if mode == "hourly":
        name = f"backup_hourly_{now.strftime('%H')}.tar.gz"
    elif mode == "manual":
        name = f"backup_manual_{now.strftime('%Y-%m-%d_%H-%M')}.tar.gz"
    else:
        name = f"backup_daily_{now.strftime('%Y-%m-%d')}.tar.gz"

    archive_path = os.path.join(base_dirs[mode], name)

    meta = {
        "timestamp": now.isoformat(),
        "type": mode,
        "stack": stack,
        "image_tag": image_tag,
        "image_id": image_id,
        "image_path": image_path,
        "database": {
            "type": db_info["type"],
            "user": db_info["user"],
            "name": db_info["name"],
            "image": db_info["image"],
            "file": db_info["file"]
        } if db_info else None,
        "directories": [
            {"directory": d["directory"], "file": d["file"]}
            for d in dirs_info
        ]
    }

    meta_tmp = os.path.join(base_dirs[mode], "meta.json")
    with open(meta_tmp, "w") as f:
        json.dump(meta, f, indent=4)

    with tarfile.open(archive_path, "w:gz") as tar:
        if db_info:
            tar.add(db_info["path"], arcname=db_info["file"])
        for d in dirs_info:
            tar.add(d["path"], arcname=d["file"])
        tar.add(meta_tmp, arcname="meta.json")

    os.remove(meta_tmp)
    success(f"Archive created: {archive_path}")
    return archive_path

# =========================================================
# === INDEX & RECONCILE ===================================
# =========================================================

def extract_meta_from_archive(path):
    try:
        with tarfile.open(path, "r:gz") as tar:
            if "meta.json" in tar.getnames():
                member = tar.getmember("meta.json")
                with tar.extractfile(member) as f:
                    return json.load(f)
    except Exception:
        return {}
    return {}

def register_backup(index_file, archive_path, mode, image_tag, image_id, image_path):
    data = load_json(index_file)
    size_mb = Path(archive_path).stat().st_size / 1024 / 1024
    # Extract image SHA256 from image file if it exists
    image_sha256 = compute_sha256(image_path) if image_path and os.path.exists(image_path) else "unknown"
    entry = {
        "type": mode,
        "timestamp": datetime.datetime.now().isoformat(),
        "archive_path": archive_path,
        "size_mb": round(size_mb, 2),
        "image_tag": image_tag,
        "image_id": image_id,
        "image_path": image_path,
        "image_sha256": image_sha256,
        "sha256": compute_sha256(archive_path),
    }
    
    # For hourly backups: remove old entry with same hour, as file gets overwritten
    # This prevents stale entries in the index when backup_hourly_HH.tar.gz is replaced
    if mode == "hourly":
        now = datetime.datetime.now()
        hour_str = now.strftime('%H')
        # Remove any existing entry for this hour from previous days
        data = [e for e in data if not (e.get("type") == "hourly" and 
                                        f"backup_hourly_{hour_str}.tar.gz" in e.get("archive_path", ""))]
        step(f"Removed stale hourly backup entry for hour {hour_str} (file gets overwritten).")
    
    data.append(entry)
    data = sorted(data, key=lambda e: e["timestamp"], reverse=True)
    save_json(index_file, data)
    step("Backup registered in index.")
    return data

def reconcile_backups(cfg, base_dirs, index_file):
    banner("🔧  Starting reconcile operation …")
    
    # Step 1: Scan filesystem for all existing backups
    existing_archives = {}
    for m in ["hourly", "daily", "manual"]:
        folder = base_dirs[m]
        if not Path(folder).exists():
            log_echo(f"   →  Backup folder {folder} does not exist, skipping {m} backups.")
            continue
        for f in Path(folder).glob("backup_*.tar.gz"):
            existing_archives[str(f.resolve())] = m
    
    step(f"Found {len(existing_archives)} existing backup archives on filesystem.")
    
    # Step 2: Build new index from filesystem (complete rebuild, no old entries)
    final_entries = []
    for path, mode in existing_archives.items():
        try:
            meta = extract_meta_from_archive(path)
            # Get image SHA256 from meta.json if available, otherwise try to compute from image_path
            image_sha256 = "unknown"
            if "image_path" in meta and meta["image_path"] and os.path.exists(meta["image_path"]):
                image_sha256 = compute_sha256(meta["image_path"])
            entry = {
                "type": mode,
                "timestamp": datetime.datetime.fromtimestamp(Path(path).stat().st_mtime).isoformat(),
                "archive_path": path,
                "size_mb": round(Path(path).stat().st_size / 1024 / 1024, 2),
                "image_tag": meta.get("image_tag", "unknown"),
                "image_id": meta.get("image_id", "unknown"),
                "image_path": meta.get("image_path", "unknown"),
                "image_sha256": image_sha256,
                "sha256": compute_sha256(path),
            }
            final_entries.append(entry)
            log_echo(f"   →  Added to index: {Path(path).name}")
        except Exception as e:
            log_echo(f"⚠️  Failed to process {path}: {e}", level="warning")
            continue
    
    # Step 3: Apply retention policy (if configured)
    retention = cfg.get("retention", {})
    if retention:
        now = datetime.datetime.now()
        filtered_entries = []
        for e in final_entries:
            t = e["type"].lower()
            ts = datetime.datetime.fromisoformat(e["timestamp"])
            age = (now - ts).days
            
            if t == "hourly" and retention.get("hourly", 0) > 0 and age > 0:
                # Hourly backups older than today are removed
                log_echo(f"   →  Removed expired hourly backup: {Path(e['archive_path']).name}")
                continue
            if t == "daily" and retention.get("daily", 0) > 0 and age > int(retention["daily"]):
                log_echo(f"   →  Removed expired daily backup: {Path(e['archive_path']).name}")
                continue
            if t == "manual" and retention.get("manual", 0) > 0 and age > int(retention["manual"]):
                log_echo(f"   →  Removed expired manual backup: {Path(e['archive_path']).name}")
                continue
            
            filtered_entries.append(e)
        final_entries = filtered_entries
    
    # Step 4: Sort by timestamp (newest first) and save
    final_entries = sorted(final_entries, key=lambda e: e["timestamp"], reverse=True)
    save_json(index_file, final_entries)
    success(f"Reconcile finished — {len(final_entries)} valid entries written to index (sorted by date).")


def list_backups(index_file):
    data = load_json(index_file)
    banner("📜  Listing Available Backups")
    if not data:
        print("No backups found.")
        return
    
    # Print header
    header = ("Timestamp        │    Size MB │ Image Tag                │ Image SHA256           ")
    print(header)
    print("─" * 100)
    
    for e in data:
        # Format timestamp as DD.MM.YYYY HH:MM
        ts_iso = e['timestamp'][:15]  # YYYY-MM-DD HH:MM
        ts_parts = ts_iso.split('T')
        date_part = ts_parts[0].split('-')  # YYYY-MM-DD
        time_part = ts_parts[1] if len(ts_parts) > 1 else "00:00"
        ts_formatted = f"{date_part[2]}.{date_part[1]}.{date_part[0]} {time_part}"
        
        size = f"{e['size_mb']:9.1f}"
        image_tag = e.get('image_tag', 'unknown')[:24]  # First 24 chars
        image_sha = e.get('image_sha256', 'unknown')[:16]  # First 16 chars
        print(f"{ts_formatted}  │ {size:11s}│ {image_tag:24s} │ {image_sha:16s}")
    
    print("─" * 100)
    print(f"\nTotal: {len(data)} backup(s) listed.\n")


def delete_backups(index_file, base_dirs):
    """Interactive backup deletion with image cleanup."""
    data = load_json(index_file)
    
    if not data:
        print("No backups found.")
        return
    
    banner("🗑️  Delete Backups")
    
    # Display all backups with indices
    print("\nAvailable backups:")
    print("─" * 100)
    print("Index │ Timestamp        │   Size MB │ Image Tag (24 chars)     │ Image SHA256 (16 chars)")
    print("─" * 100)
    
    for idx, e in enumerate(data):
        ts_iso = e['timestamp'][:16]
        ts_parts = ts_iso.split('T')
        date_part = ts_parts[0].split('-')
        time_part = ts_parts[1] if len(ts_parts) > 1 else "00:00"
        ts_formatted = f"{date_part[2]}.{date_part[1]}.{date_part[0]} {time_part}"
        
        size = f"{e['size_mb']:7.1f}"
        image_tag = e.get('image_tag', 'unknown')[:24]
        image_sha = e.get('image_sha256', 'unknown')[:16]
        print(f" {idx:4d} │ {ts_formatted} │ {size:10s}│ {image_tag:24s} │ {image_sha:16s}")
    
    print("─" * 100)
    
    # Get user input for indices to delete
    indices_input = input("\nEnter indices to delete (comma-separated, e.g. 0,2,5): ").strip()
    
    if not indices_input:
        print("No indices provided. Deletion cancelled.")
        return
    
    # Parse indices
    try:
        indices = [int(i.strip()) for i in indices_input.split(",")]
        indices = list(set(indices))  # Remove duplicates
        indices.sort(reverse=True)  # Sort reverse to delete from end first (avoid index shifts)
    except ValueError:
        print("❌ Invalid input. Please provide comma-separated numbers.")
        return
    
    # Validate indices
    invalid = [i for i in indices if i < 0 or i >= len(data)]
    if invalid:
        print(f"❌ Invalid indices: {invalid}")
        return
    
    # Collect image SHAs to check for other uses
    backups_to_delete = [data[i] for i in indices]
    image_shas_to_check = set(e.get('image_sha256') for e in backups_to_delete if e.get('image_sha256') != 'unknown')
    
    # Delete backup archives
    for idx in indices:
        e = data[idx]
        archive_path = e['archive_path']
        
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
                print(f"✅ Deleted backup: {Path(archive_path).name}")
            except Exception as ex:
                log_echo(f"❌ Failed to delete {archive_path}: {ex}", level="error")
                return
        else:
            print(f"⚠️  Backup not found: {archive_path}")
    
    # Check if image SHAs are still in use by remaining backups
    remaining_shas = set(e.get('image_sha256') for e in data if data.index(e) not in indices and e.get('image_sha256') != 'unknown')
    
    images_to_delete = image_shas_to_check - remaining_shas
    
    # Delete orphaned image archives
    for image_sha in images_to_delete:
        images_dir = base_dirs["images"]
        # Find image files matching this SHA (first 16 chars)
        for img_file in Path(images_dir).glob("*.tar.gz"):
            try:
                file_sha = compute_sha256(str(img_file))
                if file_sha == image_sha:
                    os.remove(img_file)
                    print(f"✅ Deleted orphaned image: {img_file.name}")
                    break
            except Exception:
                continue
    
    # Update index file (remove deleted entries)
    updated_data = [e for i, e in enumerate(data) if i not in indices]
    updated_data = sorted(updated_data, key=lambda e: e["timestamp"], reverse=True)
    save_json(index_file, updated_data)
    
    print(f"\n✅ Deletion complete. Index updated with {len(updated_data)} remaining backups.\n")
# === MAIN ================================================
# =========================================================

def main():
    args = parse_args()
    cfg = load_stack_config(args.stack, args.config_file)
    stack_dir = os.path.join(args.backup_base_dir, args.stack)
    base_dirs = {
        "meta": os.path.join(stack_dir, "meta"),
        "daily": os.path.join(stack_dir, "daily"),
        "hourly": os.path.join(stack_dir, "hourly"),
        "manual": os.path.join(stack_dir, "manual"),
        "images": os.path.join(stack_dir, "images"),
    }
    for d in base_dirs.values():
        ensure_dir(d)

    log_file = os.path.join(base_dirs["meta"], "stack_backup.log")

    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )

    index_file = os.path.join(base_dirs["meta"], "backups_index.json")
    mode = args.mode.lower()

    if mode == "list":
        list_backups(index_file)
        sys.exit(0)
    if mode == "delete":
        delete_backups(index_file, base_dirs)
        sys.exit(0)
    if mode == "reconcile":
        reconcile_backups(cfg, base_dirs, index_file)
        sys.exit(0)

    banner(f"🔶  Starting backup for stack '{args.stack}'  [MODE: {mode.upper()}]")
    step("Preparing environment and temporary directories ...")

    tmp_dir = Path("/tmp") / f"stack_backup_{args.stack}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # collect infos
    db_info = backup_database(args.stack, cfg, tmp_dir) if "db" in cfg else None
    dirs_info = backup_directories(cfg, tmp_dir)
    image_id, image_path = export_images(cfg, base_dirs["images"])
    image_tag = cfg.get("images", [None])[0] if cfg.get("images") else "unknown"

    archive_path = create_archive(args.stack, mode, db_info, dirs_info, image_id, image_path, image_tag, base_dirs)
    shutil.rmtree(tmp_dir)
    register_backup(index_file, archive_path, mode, image_tag, image_id, image_path)

    step("Applying retention policy ...")
    retention = cfg.get("retention", {})
    now = datetime.datetime.now()
    data = load_json(index_file)
    cleaned = []
    for e in data:
        t, ts = e["type"], datetime.datetime.fromisoformat(e["timestamp"])
        age = (now - ts).days
        if t == "daily" and retention.get("daily", 0) > 0 and age > int(retention["daily"]):
            continue
        if t == "manual" and retention.get("manual", 0) > 0 and age > int(retention["manual"]):
            continue
        cleaned.append(e)
    save_json(index_file, sorted(cleaned, key=lambda e: e["timestamp"], reverse=True))

    success("Backup completed successfully!")
    print(f"\n📦  Archive stored at:\n    {archive_path}")
    print("════════════════════════════════════════════════════════════")

if __name__ == "__main__":
    main()

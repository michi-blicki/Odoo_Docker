#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 Odoo Addons Downloader & Merger
-------------------------------------------------------------------------------
 Author:        Michael Blickenstorfer, <michi@blicki.ch>
 Created:       2025-12-05
 Last Modified: 2026-01-09
 Version:       2.0
-------------------------------------------------------------------------------
 Description:
     This script manages the download and aggregation of Odoo addons from
     multiple Git repositories. It supports both grouped repositories
     (such as OCA collections) and single-addon repositories.

     For each repository or addon:
       - Clone or update the git source.
       - Collect and merge any found requirements.txt files.
       - Copy the addon(s) to a unified target directory.

-------------------------------------------------------------------------------
 Requirements:
     - Python 3.8+
     - Git installed and accessible from PATH
-------------------------------------------------------------------------------
 Logging:
     Log output is sent both to stdout and to a log file for cron usage.
     Log file: /srv/odoo/logs/addons_downloader.log

===============================================================================
"""

import json
import os
import shutil
import subprocess
import logging
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DOWNLOADS_DIR = "/srv/odoo/downloads"
ADDONS_TARGET = "/srv/odoo/odoo-community-addons"
ADDONS_JSON   = "/srv/odoo/etc/addons.json"
REQUIREMENTS  = "/srv/odoo/etc/addons_requirements.txt"
LOG_DIR       = "/srv/odoo/logs"
LOG_FILE      = os.path.join(LOG_DIR, "addons_downloader.log")

# Ensure required directories exist
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(ADDONS_TARGET, exist_ok=True)
os.makedirs(os.path.dirname(REQUIREMENTS), exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logger = logging.getLogger("odoo_addons_downloader")
logger.setLevel(logging.INFO)

# Format for timestamps, levels, etc.
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Log to file (for cron)
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(formatter)

# Log to stdout (for interactive use)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def read_requirements_file(path):
    """
    Read a requirements.txt file and return the set of requirement lines.
    
    Args:
        path (str): Path to the requirements file.

    Returns:
        set[str]: Unique non-empty lines excluding comments.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip() and not line.startswith("#"))
    except FileNotFoundError:
        return set()


def append_requirements(new_lines):
    """
    Append new requirement lines to the global requirements file,
    avoiding duplicates.
    
    Args:
        new_lines (Iterable[str]): The requirement lines to add.
    """
    known_lines = read_requirements_file(REQUIREMENTS)
    to_add = [line for line in new_lines if line not in known_lines]
    if to_add:
        with open(REQUIREMENTS, "a", encoding="utf-8") as f:
            for line in to_add:
                f.write(line + '\n')
        logger.info(f"Appended {len(to_add)} new requirements to {REQUIREMENTS}")
    else:
        logger.info(f"No new requirements to append to {REQUIREMENTS}")


def run_git_command(args, cwd=None):
    """
    Execute a Git command and log its output.

    Args:
        args (list[str]): Git command arguments.
        cwd (str, optional): Working directory.
    """
    try:
        subprocess.run(["git"] + args, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {' '.join(args)} (in {cwd})")
        logger.error(e.stderr.decode("utf-8", errors="ignore"))
        raise


# ---------------------------------------------------------------------------
# Main Procedure
# ---------------------------------------------------------------------------

def process_addons():
    """
    Main procedure to process and synchronize all addons.
    Reads configuration from the JSON file, clones or updates
    repositories, merges requirements, and copies addon directories
    to the target Odoo addons directory.
    """
    # Read JSON configuration
    with open(ADDONS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Ensure global requirements file exists
    if os.path.exists(REQUIREMENTS):
        logger.info(f"Found global {REQUIREMENTS}.")
    else:
        open(REQUIREMENTS, "a").close()
        logger.info(f"Created new empty global requirements file at {REQUIREMENTS}.")

    # Iterate entries
    for entry in data:
        repo = entry["repo"]
        branch = entry["branch"]
        repo_dir = entry["repo_dir"]
        addons = entry.get("addons")

        if not addons:
            addons = [repo_dir]  # single-addon repositories
            addon_is_repo_root = True
        else:
            addon_is_repo_root = False

        dest_path = os.path.join(DOWNLOADS_DIR, repo_dir)

        if not os.path.exists(dest_path):
            logger.info(f"Cloning {repo} [{branch}] into {dest_path} ...")
            run_git_command(["clone", "--branch", branch, "--depth", "1", repo, dest_path])
        else:
            logger.info(f"Updating existing repo {repo_dir} ...")
            run_git_command(["fetch"], cwd=dest_path)
            run_git_command(["checkout", branch], cwd=dest_path)
            run_git_command(["pull", "origin", branch], cwd=dest_path)

        # Check for repo-level requirements
        repo_req = os.path.join(dest_path, "requirements.txt")
        if os.path.isfile(repo_req):
            lines = read_requirements_file(repo_req)
            if lines:
                logger.info(f"Found requirements.txt in repo {repo_dir}")
                append_requirements(lines)
        else:
            logger.info(f"No requirements.txt in repo {repo_dir}")

        # Process addons within the repo
        for addon in addons:
            src = dest_path if addon_is_repo_root else os.path.join(dest_path, addon)
            dst = os.path.join(ADDONS_TARGET, addon)

            if os.path.isdir(src):
                addon_req = os.path.join(src, "requirements.txt")
                if os.path.isfile(addon_req):
                    lines = read_requirements_file(addon_req)
                    if lines:
                        logger.info(f"Found requirements.txt in addon {addon}")
                        append_requirements(lines)

                if os.path.exists(dst):
                    logger.info(f"Removing existing addon directory {dst}")
                    shutil.rmtree(dst)

                shutil.copytree(src, dst)
                logger.info(f"Copied {addon} to {ADDONS_TARGET}")
            else:
                logger.warning(f"Addon {addon} not found at {src}")

    logger.info("All Odoo addons processed successfully.")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    start_time = datetime.now()
    logger.info("===== Starting Odoo Addons Downloader =====")
    logger.info(f"Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        process_addons()
    except Exception as e:
        logger.exception("An unexpected error occurred during processing.")
    finally:
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        logger.info(f"Finished at {end_time.strftime('%Y-%m-%d %H:%M:%S')} after {duration:.2f} seconds")
        logger.info("===== End of Script Execution =====")


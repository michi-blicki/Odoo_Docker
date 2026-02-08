# Docker Swarm Migration Summary

## Updated Script: recreate_odoo_test.py

### Key Changes from Docker Compose to Docker Swarm

#### 1. **Configuration Updates**
- ✅ Removed: `DEFAULT_REMOTE_HOST`, `DEFAULT_REMOTE_SSH_KEY`, `DEFAULT_DOCKER_COMPOSE_FILE`
- ✅ Added: `DEFAULT_STACK_FILE`, `DEFAULT_STACK_NAME`
- ✅ Backup path now uses local GlusterFC mount: `/srv/gfs-backup/odoo-prod`

#### 2. **Backup Loading** (`list_backups`)
- ✅ **Removed SCP transfers** - now loads directly from GlusterFC
- Reads `backups_index.json` from `/srv/gfs-backup/odoo-prod/meta/`

#### 3. **Restore Environment Preparation** (`prepare_restore_environment`)
- ✅ **Replaced**: `docker compose down` → `docker stack rm`
- ✅ **Replaced**: Compose file parsing → Stack file parsing
- ✅ Cleaner volume cleanup using stack definition device paths
- Waits 3 seconds after stack removal for full shutdown

#### 4. **Backup Recreation** (`recreate_backup`)
- ✅ **Replaced**: SCP file transfer → Local `shutil.copy2()` from GlusterFC
- ✅ **Replaced**: Image pull logic → Assumes image is already on all Swarm nodes
- Uses local file operations instead of network transfers

#### 5. **Database Restore** (`restore_database`) - **MAJOR CHANGE**
- ✅ **Replaced**: `docker compose up` → `docker stack deploy`
- ✅ **Completely redesigned health check**:
  - **OLD**: Used `docker inspect` to check running container health
  - **NEW**: Uses `docker run postgres:16-alpine pg_isready` to test connectivity
  - Temporary container tests database readiness against service DNS name
  - More reliable and doesn't depend on finding running containers
  
- ✅ **Database operations** now use temporary postgres containers:
  - `docker run --network postgres:16-alpine psql` for SQL execution
  - Eliminates need to find and access the running database container
  - Works regardless of which Swarm node hosts the database

#### 6. **Stack Deployment** (`start_stack` - was `start_containers`)
- ✅ **Replaced**: `docker compose up` → `docker stack deploy`
- ✅ Uses `docker service ls` instead of `docker ps`
- Shows service replicas instead of container list

#### 7. **Health Checking** (`check_health`)
- ✅ **Replaced**: `docker ps` → `docker service ls`
- ✅ **Replaced**: Container status checks → Service replica checks
- Validates service scaling state
- Ports and HTTP checks remain the same

#### 8. **Argument Parser**
- ✅ **Removed**: `--remote-host`, `--remote-ssh-key`, `--remote-backup-index`, `--docker-compose-file`
- ✅ **Added**: `--stack-file`, `--stack-name`
- All parameters now point to local resources

### Architecture Benefits

1. **No SCP Transfers**: All backup data accessed via GlusterFC mount
2. **Docker Swarm Native**: Uses `docker stack` and `docker service` commands
3. **Better Health Checks**: Uses `pg_isready` for more reliable database detection
4. **Temporary Containers**: Database operations don't depend on running services
5. **Multi-Node Ready**: Works on any Swarm manager node (lxmc10, lxerp01, lxcs01)

### Prerequisites

Before running the script:

1. **Docker Stack File** must exist at: `/srv/gfs-storage/odoo-test/docker-stack_odoo-test.yml`
2. **GlusterFC Mounts**:
   - `/srv/gfs-backup/odoo-prod/` - contains backups_index.json and backup archives
   - `/srv/gfs-storage/odoo-test/` - contains the stack definition
3. **Docker Image** `blicki/odoo:18.0-PROD` must be available on all Swarm nodes
4. **Swarm Network**: `odoo-test_net` will be created automatically by stack deploy

### Usage Examples

```bash
# List available backups
python3 recreate_odoo_test.py list

# Interactive menu mode
python3 recreate_odoo_test.py

# Restore specific backup
python3 recreate_odoo_test.py backup 2025-02-07T10:30:00 --start-stack

# Health check
python3 recreate_odoo_test.py health

# Debug mode
python3 recreate_odoo_test.py --verbose list
```

### Key Script Functions

| Function | Purpose | Changes |
|----------|---------|---------|
| `list_backups()` | List available backups | Removed SCP, uses local GlusterFC |
| `prepare_restore_environment()` | Clean and prepare for restore | Stack instead of Compose, better cleanup |
| `restore_database()` | Restore PostgreSQL | **NEW pg_isready approach** |
| `restore_directories()` | Extract backup files | No changes (still uses sudo) |
| `start_stack()` | Deploy services | Changed from Compose to Stack |
| `check_health()` | Verify system health | Uses `docker service` commands |
| `menu_mode()` | Interactive menu | Updated for Swarm |

### Testing Checklist

- [ ] Load backup index from GlusterFC
- [ ] List backups successfully
- [ ] Interactive menu works
- [ ] Stack removal and cleanup works
- [ ] Database restore completes
- [ ] pg_isready health check succeeds
- [ ] Directory restoration works
- [ ] Stack deploys correctly
- [ ] Health checks pass
- [ ] Odoo is accessible on ports 8069/8072

### Notes

- The `pg_isready` approach is more reliable than container inspection
- Temporary containers are cleaned up automatically with `--rm`
- Stack DNS names follow format: `{stack_name}_{service_name}`
- All GlusterFC paths are directly mounted, making the script I/O fast
- The script can run on any manager node (lxmc10, lxerp01, lxcs01)

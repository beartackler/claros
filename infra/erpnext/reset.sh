#!/usr/bin/env bash
# Snapshot / restore the seeded ERPNext site between demo runs (bench backup / restore
# inside the backend container). Snapshot lives in the `sites` volume AND is copied to
# infra/erpnext/snapshots/ on the host, so it survives `docker compose down -v`.
#
#   ./reset.sh snapshot   # after seed.py: save current DB as the golden state
#   ./reset.sh restore    # (default) roll the site back to the golden state (~30-60 s)
#   ./reset.sh list       # show snapshots
set -euo pipefail

cd "$(dirname "$0")"
SITE=frontend
DB_ROOT_PW="${DB_ROOT_PASSWORD:-admin}"
IN=/home/frappe/frappe-bench/sites/claros-snapshots        # inside the container
GOLD="$IN/claros-seeded.sql.gz"
HOST_DIR=./snapshots
dc() { docker compose -f docker-compose.yml "$@"; }
bexec() { dc exec -T backend bash -c "$1"; }

cmd="${1:-restore}"
case "$cmd" in
  snapshot)
    bexec "mkdir -p $IN/tmp && rm -f $IN/tmp/* && \
           bench --site $SITE backup --backup-path $IN/tmp && \
           mv \$(ls -1 $IN/tmp/*-database.sql.gz | head -1) $GOLD && rm -rf $IN/tmp"
    mkdir -p "$HOST_DIR"
    dc cp "backend:$GOLD" "$HOST_DIR/claros-seeded.sql.gz"
    echo "snapshot saved: $GOLD (copy: $HOST_DIR/claros-seeded.sql.gz)"
    ;;
  restore)
    if ! bexec "test -f $GOLD"; then
      if [ -f "$HOST_DIR/claros-seeded.sql.gz" ]; then
        echo "golden snapshot missing in volume -> copying from host"
        bexec "mkdir -p $IN"
        dc cp "$HOST_DIR/claros-seeded.sql.gz" "backend:$GOLD"
        dc exec -T -u root backend chown frappe:frappe "$GOLD"
      else
        echo "no snapshot yet: run ./reset.sh snapshot after seeding" >&2; exit 1
      fi
    fi
    bexec "bench --site $SITE restore $GOLD --db-root-username root --db-root-password '$DB_ROOT_PW' && \
           bench --site $SITE clear-cache"
    echo "restored $SITE to golden snapshot"
    ;;
  list)
    bexec "ls -la $IN 2>/dev/null || echo '(none in volume)'"; ls -la "$HOST_DIR" 2>/dev/null || true
    ;;
  *) echo "usage: $0 [snapshot|restore|list]" >&2; exit 2 ;;
esac

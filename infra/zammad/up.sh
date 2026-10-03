#!/usr/bin/env bash
# Start Zammad and create the admin account (idempotent).
#   ./up.sh            # without Elasticsearch (default, lighter)
#   ./up.sh --search   # with Elasticsearch
#
# Why not AUTOWIZARD_JSON: zammad-init writes auto_wizard.json into its *own* container
# filesystem, which the separate railsserver container cannot read in this split layout.
# A rails runner one-liner is deterministic instead.
set -euo pipefail
cd "$(dirname "$0")"
ADMIN_EMAIL="${ZAMMAD_ADMIN_EMAIL:-admin@helpdesk.example}"
ADMIN_PASSWORD="${ZAMMAD_ADMIN_PASSWORD:-Claros-Demo-2026!}"
PORT="${ZAMMAD_PORT:-8081}"

if [ "${1:-}" = "--search" ]; then
  export ELASTICSEARCH_ENABLED=true
  docker compose --profile search up -d
else
  docker compose up -d
fi

echo "waiting for Zammad on http://localhost:$PORT (first boot 2-5 min) ..."
for _ in $(seq 1 120); do
  curl -sf "http://localhost:$PORT/api/v1/getting_started" >/dev/null 2>&1 && break
  sleep 5
done

echo "creating admin $ADMIN_EMAIL (if missing) ..."
docker compose exec -T \
  -e ADMIN_EMAIL="$ADMIN_EMAIL" -e ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  zammad-railsserver bundle exec rails r '
    UserInfo.current_user_id = 1
    email = ENV.fetch("ADMIN_EMAIL")
    u = User.find_by(login: email)
    unless u
      u = User.create!(login: email, email: email, firstname: "Hana", lastname: "Admin",
                       password: ENV.fetch("ADMIN_PASSWORD"), active: true,
                       roles: Role.where(name: %w[Admin Agent]))
      puts "created #{email}"
    end
    Setting.set("system_init_done", true)
    Setting.set("product_name", "Lumora Helpdesk")
    puts "admin ready: #{u.login}"
  '
echo "done. Next: python seed.py   (UI: http://localhost:$PORT  $ADMIN_EMAIL / $ADMIN_PASSWORD)"

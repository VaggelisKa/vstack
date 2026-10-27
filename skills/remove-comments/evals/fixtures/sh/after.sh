#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/app}"
COUNT=$#

log() {
  echo "[$(date +%T)] $*" >&2
}

# shellcheck disable=SC2086
for host in $HOSTS; do
  rsync -a "$APP_DIR/" "$host:$APP_DIR/"
  ssh "$host" 'systemctl restart app # quoted, so not a comment'
done

cat <<EOF
# This line is part of the heredoc and must stay
Deployed to ${#HOSTS} hosts after $COUNT args
EOF

# systemd needs a moment to release the port; restarting faster fails with EADDRINUSE
sleep 2
log "done"

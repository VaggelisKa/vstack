#!/usr/bin/env bash
# deploy.sh - deploys the app
set -euo pipefail

# ------------------------------------------------------------
# Variables
# ------------------------------------------------------------
APP_DIR="${APP_DIR:-/srv/app}" # the app directory
COUNT=$# # number of args

# Function to log messages
log() {
  echo "[$(date +%T)] $*" >&2 # print to stderr
}

# shellcheck disable=SC2086
# Now handles multiple hosts
for host in $HOSTS; do
  # Copy the files
  rsync -a "$APP_DIR/" "$host:$APP_DIR/"
  # rsync -a --delete "$APP_DIR/" "$host:$APP_DIR/"
  ssh "$host" 'systemctl restart app # quoted, so not a comment'
done

cat <<EOF
# This line is part of the heredoc and must stay
Deployed to ${#HOSTS} hosts after $COUNT args
EOF

# systemd needs a moment to release the port; restarting faster fails with EADDRINUSE
sleep 2
log "done" # done

#!/usr/bin/env bash
set -euo pipefail

# Drop privileges helper
run_as_node() {
  if [ "$(id -u)" = "0" ]; then
    exec gosu node "$@"
  else
    exec "$@"
  fi
}

# Ensure ownership of /app and node_modules cache (when running as root initially)
if [ "$(id -u)" = "0" ]; then
  chown -R node:node /app || true
  chown -R node:node /app/node_modules || true
fi

# Install dependencies deterministically if needed
LOCK="/app/package-lock.json"
NM="/app/node_modules"
STAMP="/app/.lockhash"

calc_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$LOCK" | awk '{print $1}'
  else
    # fallback for alpine/busybox if you ever swap base
    openssl dgst -sha256 "$LOCK" | awk '{print $2}'
  fi
}

needs_install=0
if [ ! -d "$NM" ]; then
  needs_install=1
elif [ ! -f "$STAMP" ]; then
  needs_install=1
else
  current="$(calc_hash)"
  previous="$(cat "$STAMP" || true)"
  [ "$current" != "$previous" ] && needs_install=1 || needs_install=0
fi

if [ "$needs_install" -eq 1 ]; then
  if [ "${AUTO_UPDATE_LOCK:-0}" != "0" ]; then
    echo "AUTO_UPDATE_LOCK=1 -> refusing to mutate lock; using npm ci strictly."
  fi
  echo "Installing deps with npm ci..."
  if [ "$(id -u)" = "0" ]; then
    gosu node npm ci --no-audit --no-fund
  else
    npm ci --no-audit --no-fund
  fi
  calc_hash > "$STAMP"
fi

# Run the requested command as node user
case "${1:-dev}" in
  dev)      run_as_node npm run dev ;;
  build)    run_as_node npm run build ;;
  preview)  run_as_node npm run preview -- --host 0.0.0.0 --port "${PORT:-5173}" ;;
  *)        run_as_node "$@" ;;
esac


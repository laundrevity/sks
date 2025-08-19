#!/usr/bin/env bash
set -euo pipefail

# --- Config ---
APP_DIR="/app"
RUN_AS="node"   # drop privileges after setup
HOST="0.0.0.0"  # Vite/SvelteKit host binding
PORT="${PORT:-5173}"

# Ensure dirs exist and are writeable by non-root
mkdir -p "${APP_DIR}/node_modules"
chown -R ${RUN_AS}:${RUN_AS} "${APP_DIR}"

# npm cache dir (lives in the container user’s home)
su -s /bin/bash -c "mkdir -p ~/.npm" ${RUN_AS}

cd "${APP_DIR}"

# If there's no package.json yet (brand-new repo), do nothing special;
# you can still run the scaffolder inside the container.
if [[ ! -f package.json ]]; then
  echo "No package.json found. Skip install. (You can scaffold with: npx sv@latest create .)"
else
  # We prefer exact versions from lockfile when present.
  if [[ -f package-lock.json ]]; then
    echo "Lockfile detected. Running: npm ci"
    if ! gosu ${RUN_AS} npm ci; then
      echo "npm ci failed (lock mismatch?)."
      if [[ "${AUTO_UPDATE_LOCK:-0}" == "1" ]]; then
        echo "AUTO_UPDATE_LOCK=1 -> running 'npm install' to reconcile lockfile."
        gosu ${RUN_AS} npm install
      else
        echo "Refusing to mutate lockfile. Set AUTO_UPDATE_LOCK=1 to allow 'npm install'."
        exit 1
      fi
    fi
  else
    echo "No lockfile. Running initial 'npm install' to create package-lock.json"
    gosu ${RUN_AS} npm install
  fi
fi

# Commands:
#  - dev  : start dev server
#  - build: production build
#  - preview: preview built app
cmd="${1:-dev}"

case "${cmd}" in
  dev)
    exec gosu ${RUN_AS} npm run dev -- --host "${HOST}" --port "${PORT}"
    ;;
  build)
    exec gosu ${RUN_AS} npm run build
    ;;
  preview)
    exec gosu ${RUN_AS} npm run preview -- --host "${HOST}" --port "${PORT}"
    ;;
  *)
    # allow arbitrary commands, e.g. "bash"
    shift || true
    exec gosu ${RUN_AS} "${cmd}" "$@"
    ;;
esac


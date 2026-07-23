#!/usr/bin/env bash
# Install / update script for the FTC Parts Management System.
#
# First run on a machine: fetches the repo (via wget, no git required),
# generates .env with fresh secrets, brings up the Docker stack, and walks
# you through creating the first admin user.
#
# Subsequent runs: re-fetches the latest code and rebuilds the containers,
# leaving your .env, database, and Sheets credentials untouched.
#
# Usage:
#   wget -qO- https://raw.githubusercontent.com/Andrei0016/Part-Management-System/master/install.sh | bash
#   # or, from an already-cloned/extracted copy:
#   bash install.sh
#
# Override the install directory with PMS_DIR=/some/path.

# Re-exec under bash if launched via another shell (e.g. `sh install.sh`,
# which on Debian/Raspberry Pi OS runs dash — no `set -o pipefail` there).
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  else
    echo "ERROR: this script requires bash. Install it (e.g. 'sudo apt-get install -y bash') and re-run." >&2
    exit 1
  fi
fi

set -euo pipefail

REPO_OWNER="Andrei0016"
REPO_NAME="Part-Management-System"
REPO_BRANCH="master"
TARBALL_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}/archive/refs/heads/${REPO_BRANCH}.tar.gz"

INSTALL_DIR="${PMS_DIR:-pms}"

log()  { printf '\n==> %s\n' "$1"; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

# --- 0. If we're already sitting inside a checked-out copy of the repo,
#        operate in place instead of fetching into a subdirectory. -----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd || pwd)"
if [ -f "$SCRIPT_DIR/docker-compose.yml" ] && [ -f "$SCRIPT_DIR/wsgi.py" ]; then
  INSTALL_DIR="$SCRIPT_DIR"
  SKIP_FETCH=1
else
  SKIP_FETCH=0
fi

# --- 1. Sanity-check required tools. ----------------------------------------
command -v wget >/dev/null 2>&1 || die "wget is required. Install it (e.g. 'sudo apt-get install -y wget') and re-run."
command -v tar  >/dev/null 2>&1 || die "tar is required. Install it (e.g. 'sudo apt-get install -y tar') and re-run."

if ! command -v docker >/dev/null 2>&1; then
  die "Docker is required but not installed. Install it first: https://docs.docker.com/engine/install/ then re-run this script."
fi

dc() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    die "Docker Compose not found (need either 'docker compose' or 'docker-compose')."
  fi
}

# A real controlling terminal, even when this script itself arrived via a
# `wget | bash` pipe (which occupies stdin). Used for optional prompts and
# for the interactive create-admin step.
HAS_TTY=0
if [ -r /dev/tty ]; then HAS_TTY=1; fi

ask() {
  # ask <prompt> <default> -> echoes the answer
  local prompt="$1" default="$2" answer=""
  if [ "$HAS_TTY" -eq 1 ]; then
    read -r -p "$prompt [$default]: " answer < /dev/tty || true
  fi
  echo "${answer:-$default}"
}

gen_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  else
    head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# --- 2. Fetch the latest code (unless we're already inside the repo). ------
if [ "$SKIP_FETCH" -eq 0 ]; then
  FRESH_DIR=0
  [ -d "$INSTALL_DIR" ] || FRESH_DIR=1

  log "Fetching latest code into '$INSTALL_DIR'"
  mkdir -p "$INSTALL_DIR"
  TMP_TARBALL="$(mktemp -t pms-src.XXXXXX.tar.gz)"
  trap 'rm -f "$TMP_TARBALL"' EXIT
  wget -q -O "$TMP_TARBALL" "$TARBALL_URL" || die "Failed to download $TARBALL_URL"
  tar -xzf "$TMP_TARBALL" -C "$INSTALL_DIR" --strip-components=1
  rm -f "$TMP_TARBALL"
  trap - EXIT

  if [ "$FRESH_DIR" -eq 1 ]; then
    log "Downloaded a fresh copy to '$INSTALL_DIR'"
  else
    log "Updated existing copy in '$INSTALL_DIR' (tracked files only — .env, instance/, credentials/ are never touched, since those aren't part of the repo)"
  fi
fi

cd "$INSTALL_DIR"
mkdir -p instance credentials

# --- 3. First-time setup vs. update, based on whether .env already exists. -
if [ ! -f .env ]; then
  log "No .env found — running first-time setup"

  [ -f .env.example ] || die ".env.example is missing from the fetched copy; can't bootstrap .env."
  cp .env.example .env

  SECRET_KEY="$(gen_hex)"
  API_TOKEN="$(gen_hex)"
  # .env.example lines are simple KEY=value with no embedded '#', safe for a plain sed replace.
  sed -i "s#^SECRET_KEY=.*#SECRET_KEY=${SECRET_KEY}#" .env
  sed -i "s#^API_TOKEN=.*#API_TOKEN=${API_TOKEN}#" .env

  if [ "$HAS_TTY" -eq 1 ]; then
    echo
    echo "Optional: Google Sheets sync mirrors stock to a spreadsheet. You can skip"
    echo "this now and fill in GOOGLE_SHEETS_ID in .env later — sync just stays off"
    echo "until it's set."
    SHEETS_ID="$(ask "Google Sheets ID (blank to skip)" "")"
    if [ -n "$SHEETS_ID" ]; then
      sed -i "s#^GOOGLE_SHEETS_ID=.*#GOOGLE_SHEETS_ID=${SHEETS_ID}#" .env
      echo "Remember to drop the service-account JSON at ./credentials/google_service_account.json"
    fi
  else
    echo "No TTY available — leaving GOOGLE_SHEETS_ID blank. Edit .env later to enable Sheets sync."
  fi

  log "Generated .env with fresh SECRET_KEY and API_TOKEN"
  FIRST_INSTALL=1
else
  log ".env already exists — updating existing install (.env left untouched)"
  FIRST_INSTALL=0
fi

# --- 4. Build and (re)start the stack. --------------------------------------
log "Building and starting containers"
dc up -d --build

log "Waiting for the web service to become ready"
READY=0
for _ in $(seq 1 30); do
  if wget -q -T 5 -t 1 -O /dev/null http://localhost:5000/login 2>/dev/null; then
    READY=1
    break
  fi
  sleep 2
done

if [ "$READY" -ne 1 ]; then
  echo >&2
  echo "---- last 50 lines of 'docker compose logs web' ----" >&2
  dc logs --tail=50 web >&2 || true
  echo "------------------------------------------------------" >&2
  die "Timed out waiting for the web service. Check: (cd '$INSTALL_DIR' && docker compose logs web)"
fi
echo "Web service is up: http://localhost:5000"

# --- 5. First admin user (fresh installs only). -----------------------------
if [ "$FIRST_INSTALL" -eq 1 ]; then
  if [ "$HAS_TTY" -eq 1 ]; then
    log "Create the first admin user"
    dc exec web flask create-admin < /dev/tty
  else
    log "No TTY available — skipping admin creation"
    echo "Create it yourself with:"
    echo "  cd '$INSTALL_DIR' && docker compose exec web flask create-admin"
  fi
fi

log "Done"
echo "Web UI:      http://localhost:5000"
echo "MCP server:  http://localhost:8000/mcp"
echo "Install dir: $(pwd)"

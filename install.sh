#!/usr/bin/env bash
# Install / update script for PMS, the Part Management System.
#
#   wget -qO- https://raw.githubusercontent.com/Andrei0016/Part-Management-System/master/install.sh | bash
#   # or, from an existing install directory:
#   bash install.sh
#
# First run: checks your system, asks a few questions (site name, ports, the
# first admin account), downloads the code, generates .env, then builds and
# starts everything in Docker.
#
# Later runs: downloads the latest code and rebuilds, without asking anything
# and without touching .env or the database (instance/). A real git checkout
# (a .git directory next to this script) is never re-downloaded — update it
# with `git pull` yourself; this script then just rebuilds.
#
# Environment overrides:
#   PMS_DIR=/some/path     where to install (default: ./pms)
#   PMS_BRANCH=name        which branch to download (default: master)
#   PMS_TARBALL_URL=url    download the code from somewhere else entirely

# Re-exec under bash if launched via another shell (e.g. `sh install.sh`,
# which on Debian/Raspberry Pi OS runs dash).
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
  echo "ERROR: this script needs bash. Install it (e.g. 'sudo apt-get install -y bash') and re-run." >&2
  exit 1
fi

set -euo pipefail

# Everything lives in main(), called on the last line, so bash has read the
# whole script before running any of it. That matters for `wget ... | bash`:
# bash would otherwise read the script from stdin as it goes, and any command
# that also reads stdin (like `docker compose exec`) would swallow the rest
# of it. It also means a half-downloaded script does nothing at all.
main() {
  REPO_OWNER="Andrei0016"
  REPO_NAME="Part-Management-System"
  REPO_BRANCH="${PMS_BRANCH:-master}"
  TARBALL_URL="${PMS_TARBALL_URL:-https://github.com/${REPO_OWNER}/${REPO_NAME}/archive/refs/heads/${REPO_BRANCH}.tar.gz}"
  INSTALL_DIR="${PMS_DIR:-pms}"

  DEFAULT_WEB_PORT=5000
  DEFAULT_MCP_PORT=8000

  # ---------------------------------------------------------------------------
  # Output helpers
  # ---------------------------------------------------------------------------
  if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
  else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
  fi

  step() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
  ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
  note() { printf '  %s\n' "$1"; }
  warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }

  # fail <problem> [how to fix, one line per argument...]
  fail() {
    printf '\n%sERROR:%s %s\n' "$RED$BOLD" "$RESET" "$1" >&2
    shift
    if [ "$#" -gt 0 ]; then
      printf '\n%sHow to fix:%s\n' "$BOLD" "$RESET" >&2
      for line in "$@"; do printf '  %s\n' "$line" >&2; done
    fi
    echo >&2
    exit 1
  }

  # ---------------------------------------------------------------------------
  # Interactive input. Reads from the terminal even when the script itself
  # arrived on stdin via `wget ... | bash`.
  # ---------------------------------------------------------------------------
  HAS_TTY=0
  if ( : </dev/tty ) 2>/dev/null; then HAS_TTY=1; fi

  WORK_DIR=""
  cleanup() {
    if [ -n "$WORK_DIR" ]; then rm -rf "$WORK_DIR"; fi
    # Never leave the terminal with echo off if we're stopped mid-password.
    if [ "$HAS_TTY" -eq 1 ]; then stty echo </dev/tty 2>/dev/null || true; fi
  }
  trap cleanup EXIT
  trap 'echo; exit 130' INT TERM

  # ask <prompt> <default> -> sets REPLY
  ask() {
    local answer=""
    if [ "$HAS_TTY" -eq 1 ]; then
      read -r -p "  $1 [$2]: " answer </dev/tty || true
    fi
    REPLY="${answer:-$2}"
  }

  # read_hidden <prompt> -> sets REPLY. Echo is switched off *before* the
  # prompt appears, so even fast typing never shows on screen.
  read_hidden() {
    stty -echo </dev/tty
    printf '  %s: ' "$1"
    IFS= read -r REPLY </dev/tty || true
    stty echo </dev/tty
    echo
  }

  # ask_secret <prompt> -> sets REPLY (asks twice; must match and be non-empty)
  ask_secret() {
    local first second
    while true; do
      read_hidden "$1"; first="$REPLY"
      if [ -z "$first" ]; then warn "Can't be empty — try again."; continue; fi
      read_hidden "Repeat to confirm"; second="$REPLY"
      if [ "$first" != "$second" ]; then warn "Those didn't match — try again."; continue; fi
      REPLY="$first"
      return 0
    done
  }

  # ---------------------------------------------------------------------------
  # Small utilities
  # ---------------------------------------------------------------------------
  gen_hex() {
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 32
    elif command -v python3 >/dev/null 2>&1; then
      python3 -c 'import secrets; print(secrets.token_hex(32))'
    else
      head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
  }

  download() {  # download <url> <dest>
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL -o "$2" "$1"
    else
      wget -q -O "$2" "$1"
    fi
  }

  http_ok() {  # http_ok <url>: true if it answers with a 2xx/3xx
    if command -v curl >/dev/null 2>&1; then
      curl -fsS -o /dev/null --max-time 5 "$1" 2>/dev/null
    else
      wget -q -T 5 -t 1 -O /dev/null "$1" 2>/dev/null
    fi
  }

  port_in_use() {  # something is already listening on localhost:<port>
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  }

  valid_port() {
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
  }

  env_get() {  # env_get <key> <default>: read a value from ./.env
    local value=""
    if [ -f .env ]; then
      value="$(grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true)"
    fi
    echo "${value:-$2}"
  }

  env_set() {  # env_set <key> <value>: replace or append a line in ./.env
    local key="$1" value="$2" line found=0 tmp
    tmp="$(mktemp .env.XXXXXX)"
    while IFS= read -r line || [ -n "$line" ]; do
      if [ "${line%%=*}" = "$key" ]; then
        printf '%s=%s\n' "$key" "$value"
        found=1
      else
        printf '%s\n' "$line"
      fi
    done <.env >"$tmp"
    [ "$found" -eq 1 ] || printf '%s=%s\n' "$key" "$value" >>"$tmp"
    mv "$tmp" .env
  }

  lan_ip() {
    local ip=""
    if command -v hostname >/dev/null 2>&1; then
      ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    fi
    if [ -z "$ip" ] && command -v ipconfig >/dev/null 2>&1; then
      ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
    fi
    echo "$ip"
  }

  # ---------------------------------------------------------------------------
  # Where are we? Inside an existing copy of the repo, operate in place;
  # otherwise download into $INSTALL_DIR.
  # ---------------------------------------------------------------------------
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd || pwd)"
  SKIP_FETCH=0
  if [ -f "$SCRIPT_DIR/docker-compose.yml" ] && [ -f "$SCRIPT_DIR/wsgi.py" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
    if [ -d "$SCRIPT_DIR/.git" ]; then SKIP_FETCH=1; fi
  fi
  FIRST_INSTALL=1
  if [ -f "$INSTALL_DIR/.env" ]; then FIRST_INSTALL=0; fi

  printf '%sPMS — Part Management System installer%s\n' "$BOLD" "$RESET"
  if [ "$FIRST_INSTALL" -eq 1 ]; then
    note "New install into: $INSTALL_DIR"
  else
    note "Updating the existing install in: $INSTALL_DIR"
  fi

  # ---------------------------------------------------------------------------
  # 1. Preflight checks — everything that could stop the install, checked
  #    before anything is downloaded or changed.
  # ---------------------------------------------------------------------------
  step "Checking your system"

  OS="$(uname -s)"

  if [ "$SKIP_FETCH" -eq 0 ]; then
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
      fail "Neither curl nor wget is installed, so the code can't be downloaded." \
        "Install one of them, e.g.:  sudo apt-get install -y curl"
    fi
    command -v tar >/dev/null 2>&1 || fail "tar is not installed." "Install it, e.g.:  sudo apt-get install -y tar"
    ok "Download tools found"
  fi

  if ! command -v docker >/dev/null 2>&1; then
    if [ "$OS" = "Darwin" ]; then
      fail "Docker is not installed." \
        "Install Docker Desktop: https://docs.docker.com/desktop/setup/install/mac-install/" \
        "Start it once, then re-run this installer."
    fi
    fail "Docker is not installed." \
      "Install it with Docker's official script:" \
      "  curl -fsSL https://get.docker.com | sudo sh" \
      "  sudo usermod -aG docker \$USER     # so you can use docker without sudo" \
      "Then log out and back in, and re-run this installer." \
      "(Other options: https://docs.docker.com/engine/install/)"
  fi

  if ! DOCKER_ERR="$(docker info 2>&1 >/dev/null)"; then
    case "$DOCKER_ERR" in
      *"permission denied"*|*"Permission denied"*)
        fail "Your user isn't allowed to use Docker." \
          "Add yourself to the docker group:" \
          "  sudo usermod -aG docker \$USER" \
          "then log out and back in (or run 'newgrp docker'), and re-run this installer."
        ;;
      *)
        if [ "$OS" = "Darwin" ]; then
          fail "Docker is installed but not running." \
            "Start Docker Desktop, wait until it says it's running, then re-run this installer."
        fi
        fail "Docker is installed but not running." \
          "Start it and make it start on boot:" \
          "  sudo systemctl enable --now docker" \
          "then re-run this installer."
        ;;
    esac
  fi
  ok "Docker is installed and running"

  if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
  else
    fail "Docker Compose is not installed." \
      "Install the compose plugin, e.g.:  sudo apt-get install -y docker-compose-plugin" \
      "(See https://docs.docker.com/compose/install/linux/)"
  fi
  dc() { "${DC[@]}" "$@"; }
  ok "Docker Compose found"

  if [ "$SKIP_FETCH" -eq 0 ]; then
    PARENT_DIR="$(dirname "$INSTALL_DIR")"
    mkdir -p "$PARENT_DIR" 2>/dev/null || true
    if [ ! -w "$PARENT_DIR" ] || { [ -d "$INSTALL_DIR" ] && [ ! -w "$INSTALL_DIR" ]; }; then
      fail "Can't write to '$INSTALL_DIR'." \
        "Pick a directory you own, e.g.:  PMS_DIR=\$HOME/pms bash install.sh"
    fi
  fi

  # ---------------------------------------------------------------------------
  # 2. Questions (first install only), asked before the slow part so you can
  #    walk away while it builds.
  # ---------------------------------------------------------------------------
  next_free_port() {  # next_free_port <start> [port to skip]
    local port="$1"
    while port_in_use "$port" || [ "$port" = "${2:-}" ]; do port=$((port + 1)); done
    echo "$port"
  }

  ask_port() {  # ask_port <label> <default> [other port to avoid] -> sets REPLY
    local label="$1" suggestion avoid="${3:-}"
    suggestion="$(next_free_port "$2" "$avoid")"
    if [ "$suggestion" != "$2" ]; then
      note "Port $2 is taken on this machine, so $suggestion is suggested instead."
    fi
    while true; do
      ask "$label" "$suggestion"
      if ! valid_port "$REPLY"; then
        warn "'$REPLY' isn't a valid port number (1-65535)."
      elif [ "$REPLY" = "$avoid" ]; then
        warn "Port $REPLY is already used for the web interface. Pick another one."
      elif port_in_use "$REPLY"; then
        warn "Port $REPLY is already in use by another program on this machine."
      else
        return 0
      fi
    done
  }

  if [ "$FIRST_INSTALL" -eq 1 ]; then
    SITE_TITLE="PMS"
    WEB_PORT="$DEFAULT_WEB_PORT"
    MCP_PUBLIC_PORT="$DEFAULT_MCP_PORT"
    ADMIN_USER=""; ADMIN_NAME=""; ADMIN_PASSWORD=""

    if [ "$HAS_TTY" -eq 1 ]; then
      step "A few questions (press Enter to accept the [default])"

      while true; do
        ask "Site name, shown in the browser tab and menu bar" "$SITE_TITLE"
        case "$REPLY" in
          *[\$\"\'\`\\\#]*) warn "Please leave out these characters: \$ \" ' \` \\ #" ;;
          *) SITE_TITLE="$REPLY"; break ;;
        esac
      done

      ask_port "Web interface port" "$WEB_PORT"
      WEB_PORT="$REPLY"
      ask_port "MCP server port (for Claude and other AI clients)" "$MCP_PUBLIC_PORT" "$WEB_PORT"
      MCP_PUBLIC_PORT="$REPLY"

      echo
      note "Now the first admin account, used to log in and manage everything else."
      while true; do
        ask "Admin username" "admin"
        case "$REPLY" in
          *[[:space:]]*) warn "Usernames can't contain spaces." ;;
          *) ADMIN_USER="$REPLY"; break ;;
        esac
      done
      ask "Admin's full name" "Administrator"
      ADMIN_NAME="$REPLY"
      ask_secret "Admin password"
      ADMIN_PASSWORD="$REPLY"
    else
      step "No terminal available — using defaults"
      note "Site name '$SITE_TITLE', web port $WEB_PORT, MCP port $MCP_PUBLIC_PORT."
      note "The admin account will need to be created afterwards (instructions at the end)."
    fi
  else
    # Updating: read the ports this install already uses.
    WEB_PORT="$(cd "$INSTALL_DIR" && env_get WEB_PORT "$DEFAULT_WEB_PORT")"
    MCP_PUBLIC_PORT="$(cd "$INSTALL_DIR" && env_get MCP_PUBLIC_PORT "$DEFAULT_MCP_PORT")"
  fi

  # Ports must be free — unless it's this install's own containers holding them.
  STACK_RUNNING=""
  if [ "$FIRST_INSTALL" -eq 0 ]; then
    STACK_RUNNING="$(cd "$INSTALL_DIR" && dc ps -q 2>/dev/null || true)"
  fi
  if [ -z "$STACK_RUNNING" ]; then
    for port in "$WEB_PORT" "$MCP_PUBLIC_PORT"; do
      if port_in_use "$port"; then
        if [ "$FIRST_INSTALL" -eq 1 ]; then
          fail "Port $port is already in use by another program." \
            "Stop that program, or run this installer in a terminal and choose a different port."
        fi
        fail "Port $port is already in use by another program." \
          "Stop that program, or change WEB_PORT / MCP_PUBLIC_PORT in $INSTALL_DIR/.env, then re-run."
      fi
    done
    ok "Ports $WEB_PORT and $MCP_PUBLIC_PORT are free"
  fi

  # ---------------------------------------------------------------------------
  # 3. Download the latest code.
  # ---------------------------------------------------------------------------
  if [ "$SKIP_FETCH" -eq 0 ]; then
    step "Downloading the latest code"
    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pms-install.XXXXXX")"

    download "$TARBALL_URL" "$WORK_DIR/src.tar.gz" ||
      fail "Couldn't download $TARBALL_URL" "Check this machine's internet connection, then re-run."
    mkdir "$WORK_DIR/src"
    tar -xzf "$WORK_DIR/src.tar.gz" -C "$WORK_DIR/src" ||
      fail "The downloaded archive is damaged." "Just re-run the installer."

    # GitHub archives contain a single top-level directory.
    SRC=""
    for entry in "$WORK_DIR"/src/*; do SRC="$entry"; done
    [ -f "$SRC/docker-compose.yml" ] || fail "The downloaded archive doesn't look like PMS ($TARBALL_URL)."

    mkdir -p "$INSTALL_DIR"
    # Copy everything except install.sh, which may be this very script: it's
    # moved into place separately so the replacement gets a fresh inode and
    # bash keeps reading the old (still-open) file to the end.
    mv "$SRC/install.sh" "$WORK_DIR/install.sh"
    cp -R "$SRC/." "$INSTALL_DIR/"
    mv "$WORK_DIR/install.sh" "$INSTALL_DIR/install.sh"
    chmod +x "$INSTALL_DIR/install.sh"

    rm -rf "$WORK_DIR"
    WORK_DIR=""
    ok "Code is up to date in $INSTALL_DIR (your .env and database are never touched)"
  fi

  cd "$INSTALL_DIR"
  INSTALL_DIR="$(pwd)"
  mkdir -p instance

  # ---------------------------------------------------------------------------
  # 4. Configuration (.env).
  # ---------------------------------------------------------------------------
  if [ "$FIRST_INSTALL" -eq 1 ]; then
    step "Writing configuration"
    [ -f .env.example ] || fail ".env.example is missing from the downloaded code; can't create .env."
    cp .env.example .env
    chmod 600 .env  # holds SECRET_KEY
    env_set SECRET_KEY "$(gen_hex)"
    env_set SITE_TITLE "$SITE_TITLE"
    env_set WEB_PORT "$WEB_PORT"
    env_set MCP_PUBLIC_PORT "$MCP_PUBLIC_PORT"
    ok "Created .env with a fresh secret key"
  fi

  # ---------------------------------------------------------------------------
  # 5. Build and start.
  # ---------------------------------------------------------------------------
  step "Building and starting PMS (the first build can take a few minutes)"
  dc up -d --build --remove-orphans ||
    fail "Docker couldn't build or start PMS (see the output above)." \
      "If it's a network error while installing packages, just re-run the installer."

  WEB_URL="http://localhost:$WEB_PORT"
  printf '  Waiting for the web interface'
  READY=0
  for _ in $(seq 1 45); do
    if http_ok "$WEB_URL/login"; then READY=1; break; fi
    printf '.'
    sleep 2
  done
  echo
  if [ "$READY" -ne 1 ]; then
    echo "---- last 40 lines of the web service log ----" >&2
    dc logs --tail=40 web >&2 || true
    echo "----------------------------------------------" >&2
    fail "The web interface didn't come up within 90 seconds." \
      "Look at the log above, or follow it live with:" \
      "  cd '$INSTALL_DIR' && ${DC[*]} logs -f web"
  fi
  ok "PMS is running"

  # ---------------------------------------------------------------------------
  # 6. First admin account.
  # ---------------------------------------------------------------------------
  CREATE_ADMIN_CMD="cd '$INSTALL_DIR' && ${DC[*]} exec web flask create-admin"

  if [ "$FIRST_INSTALL" -eq 1 ] && [ -n "$ADMIN_USER" ]; then
    step "Creating the admin account"
    # The password goes through the environment, never the command line, so it
    # doesn't show up in process listings.
    if PMS_ADMIN_PASSWORD="$ADMIN_PASSWORD" dc exec -T -e PMS_ADMIN_PASSWORD web \
        flask create-admin --username "$ADMIN_USER" --full-name "$ADMIN_NAME" </dev/null >/dev/null; then
      ok "Admin '$ADMIN_USER' created"
    else
      warn "Couldn't create the admin account (see above). Create it with:"
      note "  $CREATE_ADMIN_CMD"
    fi
    ADMIN_PASSWORD=""
  elif [ "$FIRST_INSTALL" -eq 0 ]; then
    # Resume an install that never got its admin (e.g. it was cancelled).
    ADMINS="$(dc exec -T web python -c 'from app import create_app; from app.models import User; app = create_app(); app.app_context().push(); print(User.query.filter_by(is_admin=True, is_active=True).count())' \
      </dev/null 2>/dev/null | tr -dc '0-9' || true)"
    if [ "$ADMINS" = "0" ]; then
      step "There's no admin account yet"
      if [ "$HAS_TTY" -eq 1 ]; then
        dc exec web flask create-admin </dev/tty || warn "Admin not created. Try again with: $CREATE_ADMIN_CMD"
      else
        note "Create one with:  $CREATE_ADMIN_CMD"
      fi
    fi
  fi

  # ---------------------------------------------------------------------------
  # 7. Summary.
  # ---------------------------------------------------------------------------
  LAN="$(lan_ip)"
  step "All done!"
  echo
  printf '  %sOpen PMS in your browser:%s\n' "$BOLD" "$RESET"
  echo "    On this machine:     $WEB_URL"
  if [ -n "$LAN" ]; then echo "    From other devices:  http://$LAN:$WEB_PORT"; fi
  echo
  printf '  %sMCP server (for Claude and other AI clients):%s\n' "$BOLD" "$RESET"
  echo "    http://${LAN:-localhost}:$MCP_PUBLIC_PORT/mcp"
  echo "    Each person connects with their own API key: log in as an admin and"
  echo "    open API keys in the menu."
  if [ "$FIRST_INSTALL" -eq 1 ] && [ -z "$ADMIN_USER" ]; then
    echo
    printf '  %sNext: create the first admin account:%s\n' "$BOLD" "$RESET"
    echo "    $CREATE_ADMIN_CMD"
  fi
  echo
  printf '  %sUseful commands%s (run them in %s):\n' "$BOLD" "$RESET" "$INSTALL_DIR"
  echo "    Update to the latest version:  bash install.sh"
  echo "    View logs:                     ${DC[*]} logs -f"
  echo "    Stop / start:                  ${DC[*]} stop  /  ${DC[*]} start"
  printf '    %sYour data lives in %s/instance — back that folder up.%s\n' "$DIM" "$INSTALL_DIR" "$RESET"
}

main "$@"

# PMS — Part Management System

A simple part manager for small hobby warehouses: track parts and stock
levels across boxes and shelves, with a web UI and an MCP server so Claude
can check and manage stock for you. Runs on small self-hosted hardware — a
spare mini PC, a Raspberry Pi, a shelf server.

## Install

On the machine that will host it (Linux, with [Docker](https://docs.docker.com/engine/install/)
already installed):

```bash
wget -qO- https://raw.githubusercontent.com/Andrei0016/Part-Management-System/master/install.sh | bash
```

The installer first checks that Docker is installed, running, and usable
by your user, and that the ports are free. If something's wrong, it tells
you exactly how to fix it. It then asks a few questions (press Enter to
accept each default):

- the site name shown in the browser,
- the ports for the web interface (default 5000) and the MCP server (default 8000),
- a username and password for the first admin account.

After that it downloads the code into `./pms`, generates a `.env` with a fresh
secret key, builds and starts everything, and prints the addresses to open,
including one for other devices on your network.

Run the same command again any time to update. Updates ask no questions and
never touch your `.env` or your data (`pms/instance/`). To install somewhere
other than `./pms`, set `PMS_DIR`, e.g. `PMS_DIR=$HOME/pms`.

Once it's up:

- Web UI: http://localhost:5000
- MCP server (Streamable-HTTP): http://localhost:8000/mcp

(Or whichever ports you picked. They're `WEB_PORT` and `MCP_PUBLIC_PORT` in `.env`.)

### Roles

| Role    | Browse parts | Take/add stock | Register/edit/delete parts and boxes | Users, API keys, activity log |
|---------|:-:|:-:|:-:|:-:|
| Visitor | ✅ | | | |
| Member  | ✅ | ✅ | | |
| Editor  | ✅ | ✅ | ✅ | |
| Admin   | ✅ | ✅ | ✅ | ✅ |

API keys (used by the MCP server) act with their owner's role. A key whose
owner has been deactivated can only read.

### Manual install

```bash
git clone https://github.com/Andrei0016/Part-Management-System.git pms
cd pms
cp .env.example .env          # edit SECRET_KEY and anything else you want to change
mkdir -p instance
docker compose up -d --build
docker compose exec web flask create-admin
docker compose exec web flask create-api-key   # only needed if an MCP client will connect
```

### Local development (no Docker)

```bash
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
flask --app wsgi.py create-admin
python wsgi.py                                    # runs on :5000 with debug=True
```

See `.env.example` for all configuration options (site title, ports, etc).

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest                                   # app, API, MCP server, migrations, CLI, installer checks
PMS_E2E=1 pytest tests/test_install_e2e.py   # full install with real Docker (takes a few minutes)
```

>[!NOTE]
>This is almost entirely vibecoded so expect bugs. If you find any please let me know by making an issue. Thank you :)!

# PMS — Part Management System

A simple part manager for small hobby warehouses: track parts and stock
levels across boxes and shelves, with a web UI, an optional read-only Google
Sheets mirror, and an MCP server so Claude can check and manage stock for
you. Runs on small self-hosted hardware — a spare mini PC, a Raspberry Pi,
a shelf server.

## Install

On the machine that will host it (Linux, with [Docker](https://docs.docker.com/engine/install/)
already installed):

```bash
wget -qO- https://raw.githubusercontent.com/Andrei0016/Part-Management-System/master/install.sh | bash
```

This downloads the code into `./pms`, generates a `.env` with fresh secrets,
builds and starts the Docker stack, and walks you through creating your
first admin user. Run the same command again any time to update.

Once it's up:

- Web UI: http://localhost:5000
- MCP server (Streamable-HTTP): http://localhost:8000/mcp

### Manual install

```bash
git clone https://github.com/Andrei0016/Part-Management-System.git pms
cd pms
cp .env.example .env          # edit SECRET_KEY, API_TOKEN, and anything else you want to change
mkdir -p instance credentials
docker compose up -d --build
docker compose exec web flask create-admin
```

### Local development (no Docker)

```bash
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
flask --app wsgi.py create-admin
python wsgi.py                                    # runs on :5000 with debug=True
```

See `.env.example` for all configuration options (site title, Google Sheets
sync, MCP account, etc).

>[!NOTE]
>This is almost entirely vibecoded so expect bugs. If you find any please let me know by making an issue. Thank you :)!

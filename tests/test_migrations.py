"""Upgrading old on-disk schemas. Each test hand-builds a database the way an
older release left it, then boots the current app against it."""
import sqlite3

from app import SCHEMA_VERSION, create_app
from conftest import make_config


def _boot(path):
    create_app(make_config(path))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _users(conn):
    return {r["username"]: dict(r) for r in conn.execute("SELECT * FROM user")}


def test_v0_upgrade(tmp_path):
    path = tmp_path / "v0.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80) UNIQUE NOT NULL,
            full_name VARCHAR(120) NOT NULL, password_hash VARCHAR(255) NOT NULL,
            is_admin BOOLEAN NOT NULL, is_active BOOLEAN NOT NULL);
        INSERT INTO user VALUES (1, 'boss', 'Boss', 'x', 1, 1), (2, 'pat', 'Pat', 'x', 0, 1),
                                (3, 'mcp', 'MCP', 'x', 0, 1);
        CREATE TABLE box (box_id VARCHAR(20) PRIMARY KEY, shelf VARCHAR(50) NOT NULL, row VARCHAR(50) NOT NULL);
        INSERT INTO box VALUES ('001', 'A', '1');
        CREATE TABLE part (id VARCHAR(50) PRIMARY KEY, box_id VARCHAR(20) NOT NULL REFERENCES box(box_id),
            name VARCHAR(200) NOT NULL, quantity INTEGER NOT NULL, minimum_quantity INTEGER NOT NULL,
            tags VARCHAR(300) NOT NULL DEFAULT '');
        INSERT INTO part VALUES ('001-001', '001', 'Bolt', 5, 1, 'metal'), ('001-002', '001', 'Nut', 3, 0, '');
        CREATE TABLE log_entry (id INTEGER PRIMARY KEY, timestamp DATETIME NOT NULL,
            user_id INTEGER NOT NULL REFERENCES user(id), action VARCHAR(20) NOT NULL,
            part_id VARCHAR(50), quantity_delta INTEGER, note VARCHAR(300));
        INSERT INTO log_entry VALUES (1, '2025-01-01 00:00:00', 2, 'take', '001-001', -1, NULL);
    """)
    conn.commit()
    conn.close()

    conn = _boot(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    users = _users(conn)
    assert (users["boss"]["is_admin"], users["boss"]["is_member"]) == (1, 0)
    assert (users["pat"]["is_editor"], users["pat"]["is_member"]) == (0, 1)  # old visitors keep stock access
    assert users["mcp"]["is_active"] == 0
    parts = {(r["box_id"], r["id"]): r["name"] for r in conn.execute("SELECT * FROM part")}
    assert parts == {("001", "001"): "Bolt", ("001", "002"): "Nut"}
    log_user_col = next(c for c in conn.execute("PRAGMA table_info(log_entry)") if c[1] == "user_id")
    assert log_user_col[3] == 0  # now nullable
    assert conn.execute("SELECT count(*) FROM log_entry").fetchone()[0] == 1


def test_v2_upgrade_promotes_only_visitors(tmp_path):
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80) UNIQUE NOT NULL,
            full_name VARCHAR(120) NOT NULL, password_hash VARCHAR(255) NOT NULL,
            is_admin BOOLEAN NOT NULL, is_active BOOLEAN NOT NULL, is_editor BOOLEAN NOT NULL DEFAULT 0);
        INSERT INTO user VALUES (1, 'boss', 'B', 'x', 1, 1, 0), (2, 'ed', 'E', 'x', 0, 1, 1),
                                (3, 'pat', 'P', 'x', 0, 1, 0), (4, 'old', 'O', 'x', 0, 0, 0);
        PRAGMA user_version = 2;
    """)
    conn.commit()
    conn.close()

    users = _users(_boot(path))
    assert users["boss"]["is_member"] == 0 and users["ed"]["is_member"] == 0
    assert users["pat"]["is_member"] == 1 and users["old"]["is_member"] == 1


def test_fresh_database_and_reboot_are_noops(tmp_path):
    path = tmp_path / "fresh.db"
    conn = _boot(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.execute("INSERT INTO user (username, full_name, password_hash, is_admin, is_editor, is_member, is_active)"
                  " VALUES ('v', 'V', 'x', 0, 0, 0, 1)")
    conn.commit()
    conn.close()
    # A second boot must not re-run v3 and promote this deliberate visitor.
    assert _users(_boot(path))["v"]["is_member"] == 0


def test_rollback_journal_mode(tmp_path):
    conn = _boot(tmp_path / "journal.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_existing_wal_database_switched_back(tmp_path):
    """Older versions put the database in WAL mode, which corrupts it on
    Docker Desktop bind mounts. Booting must switch it back."""
    path = tmp_path / "old-wal.db"
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    conn.close()
    assert _boot(path).execute("PRAGMA journal_mode").fetchone()[0] == "delete"

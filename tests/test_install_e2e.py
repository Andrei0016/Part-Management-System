"""End-to-end: run install.sh for real against Docker, answer its questions
through a pseudo-terminal, then use the running stack exactly as a person
and an MCP client would. Builds images and takes a few minutes, so it only
runs when asked:

    PMS_E2E=1 pytest tests/test_install_e2e.py

The code is served to the installer from a tarball of this working tree (not
downloaded from GitHub), each run uses its own compose project and free
ports, and everything is torn down afterwards.
"""
import asyncio
import errno
import functools
import http.server
import json
import os
import pty
import re
import select
import socket
import subprocess
import tarfile
import threading
import time
import uuid

import pytest
import requests

from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(os.environ.get("PMS_E2E") != "1", reason="set PMS_E2E=1 to run (needs Docker)")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def tarball_url(tmp_path_factory):
    """Serve this working tree the way GitHub serves an archive."""
    out = tmp_path_factory.mktemp("tarball")
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    with tarfile.open(out / "src.tar.gz", "w:gz") as tar:
        for f in files:
            if os.path.isfile(os.path.join(REPO_ROOT, f)):
                tar.add(os.path.join(REPO_ROOT, f), arcname=f"Part-Management-System-master/{f}")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/src.tar.gz"
    server.shutdown()


class Terminal:
    """Just enough of `expect` to drive the installer's prompts."""

    def __init__(self, argv, cwd, env):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child: the pty is now its controlling terminal
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        self.output = ""
        self._pos = 0

    def expect(self, pattern, timeout=30):
        deadline = time.time() + timeout
        regex = re.compile(pattern)
        while True:
            match = regex.search(self.output, self._pos)
            if match:
                self._pos = match.end()
                return match
            remaining = deadline - time.time()
            if remaining <= 0:
                raise AssertionError(f"timed out waiting for {pattern!r}; output so far:\n{self.output[-3000:]}")
            ready, _, _ = select.select([self.fd], [], [], min(remaining, 1))
            if ready:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError as e:
                    if e.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    raise AssertionError(f"installer exited while waiting for {pattern!r}:\n{self.output[-3000:]}")
                self.output += chunk.decode(errors="replace")

    def send(self, line):
        os.write(self.fd, (line + "\n").encode())

    def wait(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ready, _, _ = select.select([self.fd], [], [], 1)
                if ready:
                    self.output += os.read(self.fd, 4096).decode(errors="replace")
            except OSError:
                pass
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
        raise AssertionError("installer didn't exit")


class Stack:
    def __init__(self, root, tarball_url):
        self.project = f"pmse2e{uuid.uuid4().hex[:8]}"
        self.dir = root / "pms"
        self.web_port, self.mcp_port = _free_port(), _free_port()
        self.env = {
            **os.environ,
            "PMS_DIR": str(self.dir),
            "PMS_TARBALL_URL": tarball_url,
            "COMPOSE_PROJECT_NAME": self.project,
            "TERM": "dumb",
        }
        self.root = root

    @property
    def web(self):
        return f"http://localhost:{self.web_port}"

    def compose(self, *args, **kwargs):
        return subprocess.run(["docker", "compose", *args], cwd=self.dir, env=self.env,
                              capture_output=True, text=True, **kwargs)

    def install_noninteractive(self, script):
        return subprocess.run(["bash", str(script)], cwd=self.root, env=self.env, capture_output=True,
                              text=True, timeout=900, start_new_session=True)

    def make_key(self, username, role=None):
        """Create a user (if role given) and an API key inside the web container."""
        code = (
            "import sys\n"
            "from app import create_app\n"
            "from app.admin import _role_to_flags\n"
            "from app.api_keys import create_api_key\n"
            "from app.extensions import db\n"
            "from app.models import User\n"
            "with create_app().app_context():\n"
            "    user = User.query.filter_by(username=sys.argv[1]).first()\n"
            "    if user is None:\n"
            "        user = User(username=sys.argv[1], full_name=sys.argv[1], **_role_to_flags(sys.argv[2]))\n"
            "        user.set_password('x'); db.session.add(user); db.session.commit()\n"
            "    print(create_api_key('e2e ' + sys.argv[1], owner_user=user)[1])\n"
        )
        result = self.compose("exec", "-T", "web", "python", "-c", code, username, role or "visitor")
        match = re.search(r"\b[0-9a-f]{64}\b", result.stdout)
        assert match, f"no key in output:\n{result.stdout}\n{result.stderr}"
        return match.group(0)

    def down(self):
        if self.dir.exists():
            self.compose("down", "--rmi", "local", "--volumes", timeout=300)


@pytest.fixture
def stack(tmp_path, tarball_url):
    stack = Stack(tmp_path, tarball_url)
    yield stack
    stack.down()


def _login(base, username, password):
    session = requests.Session()
    page = session.get(f"{base}/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    resp = session.post(f"{base}/login", data={"csrf_token": token, "username": username, "password": password},
                        allow_redirects=False)
    return session, resp


async def _mcp_session_run(url, key, calls):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    results = {}
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {key}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            results["tools"] = {t.name for t in (await session.list_tools()).tools}
            for name, args in calls:
                result = await session.call_tool(name, args)
                text = result.content[0].text if result.content else ""
                results[name] = (result.isError, text)
    return results


def test_guided_install_then_use_then_update(stack, tmp_path):
    script = tmp_path / "install.sh"
    script.write_bytes(open(os.path.join(REPO_ROOT, "install.sh"), "rb").read())

    term = Terminal(["bash", str(script)], cwd=tmp_path, env=stack.env)
    term.expect(r"Docker is installed and running")
    term.expect(r"Site name.*\]: ")
    term.send("Bad $ name")
    term.expect(r"leave out these characters")
    term.expect(r"Site name.*\]: ")
    term.send("Test Shop & Co")
    term.expect(r"Web interface port.*\]: ")
    term.send("abc")
    term.expect(r"isn't a valid port")
    term.expect(r"Web interface port.*\]: ")
    term.send(str(stack.web_port))
    term.expect(r"MCP server port.*\]: ")
    term.send(str(stack.web_port))
    term.expect(r"already used for the web interface")
    term.expect(r"MCP server port.*\]: ")
    term.send(str(stack.mcp_port))
    term.expect(r"Admin username.*\]: ")
    term.send("boss")
    term.expect(r"full name.*\]: ")
    term.send("Boss Person")
    term.expect(r"Admin password: ")
    term.send("")
    term.expect(r"Can't be empty")
    term.expect(r"Admin password: ")
    term.send("secret-1")
    term.expect(r"Repeat to confirm: ")
    term.send("different")
    term.expect(r"didn't match")
    term.expect(r"Admin password: ")
    term.send("secret-1")
    term.expect(r"Repeat to confirm: ")
    term.send("secret-1")
    term.expect(r"Admin 'boss' created", timeout=900)
    term.expect(r"All done!")
    assert term.wait() == 0, term.output
    assert "secret-1" not in term.output  # the password is never echoed

    # --- configuration -----------------------------------------------------
    env_file = stack.dir / ".env"
    env = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line and line[0] != "#")
    assert env["SITE_TITLE"] == "Test Shop & Co"
    assert (env["WEB_PORT"], env["MCP_PUBLIC_PORT"]) == (str(stack.web_port), str(stack.mcp_port))
    assert re.fullmatch(r"[0-9a-f]{64}", env["SECRET_KEY"])
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert f"http://localhost:{stack.web_port}" in term.output

    # --- web UI ------------------------------------------------------------
    _, resp = _login(stack.web, "boss", "wrong")
    assert resp.status_code == 200 and "Invalid username or password" in resp.text
    session, resp = _login(stack.web, "boss", "secret-1")
    assert resp.status_code == 302
    home = session.get(stack.web + "/").text
    assert "Test Shop &amp; Co" in home and "API keys" in home
    assert ".login-card" in requests.get(stack.web + "/static/custom.css").text

    # --- MCP server, through its own container to the web container --------
    mcp_url = f"http://localhost:{stack.mcp_port}/mcp"
    assert requests.post(mcp_url, json={}).status_code == 401
    created = stack.compose("exec", "-T", "web", "flask", "create-api-key", "--label", "cli", "--username", "boss")
    assert created.returncode == 0, created.stderr
    admin_key = re.search(r"\b[0-9a-f]{64}\b", created.stdout).group(0)

    results = asyncio.run(_mcp_session_run(mcp_url, admin_key, [
        ("register_box", {"box_id": "E1", "shelf": "S1", "row": "R1"}),
        ("register_part", {"part_id": "1", "box_id": "E1", "name": "Gear", "quantity": 10, "minimum_quantity": 5}),
        ("take_stock", {"part_id": "1", "box_id": "E1", "amount": 7, "note": "e2e"}),
        ("list_low_stock", {}),
    ]))
    assert len(results["tools"]) == 12
    for name in ("register_box", "register_part", "take_stock", "list_low_stock"):
        assert results[name][0] is False, (name, results[name])
    assert json.loads(results["take_stock"][1])["part"]["quantity"] == 3
    assert "Gear" in results["list_low_stock"][1]

    visitor_key = stack.make_key("viewer", "visitor")
    check = requests.get(f"{stack.web}/api/parts", headers={"Authorization": f"Bearer {visitor_key}"})
    assert check.status_code == 200, (check.status_code, check.text)
    results = asyncio.run(_mcp_session_run(mcp_url, visitor_key, [
        ("search_parts", {"query": "gear"}),
        ("take_stock", {"part_id": "1", "box_id": "E1", "amount": 1}),
    ]))
    assert results["search_parts"][0] is False and "Gear" in results["search_parts"][1], results
    assert results["take_stock"][0] is True and "not allowed" in results["take_stock"][1]

    # --- update: no questions, .env and data kept ---------------------------
    env_before = env_file.read_text()
    # Piped, like the documented `wget -qO- .../install.sh | bash`.
    update = subprocess.run(["bash"], input=(stack.dir / "install.sh").read_text(), cwd=stack.dir,
                            env=stack.env, capture_output=True, text=True, timeout=900, start_new_session=True)
    assert update.returncode == 0, update.stdout + update.stderr
    assert "Updating the existing install" in update.stdout
    assert "A few questions" not in update.stdout
    assert "All done!" in update.stdout
    assert env_file.read_text() == env_before
    resp = requests.get(f"{stack.web}/api/parts/E1/1", headers={"Authorization": f"Bearer {admin_key}"})
    assert resp.json()["part"]["quantity"] == 3

    # --- re-running with no active admin offers to create one ---------------
    stack.compose("exec", "-T", "web", "python", "-c", (
        "from app import create_app\n"
        "from app.extensions import db\n"
        "from app.models import User\n"
        "with create_app().app_context():\n"
        "    User.query.filter_by(username='boss').one().is_active = False\n"
        "    db.session.commit()\n"
    ), check=True)
    term = Terminal(["bash", str(stack.dir / "install.sh")], cwd=stack.dir, env=stack.env)
    term.expect(r"There's no admin account yet", timeout=900)
    term.expect(r"Username: ")
    term.send("boss2")
    term.expect(r"Full name: ")
    term.send("Second Boss")
    term.expect(r"Password: ")
    term.send("secret-2")
    term.expect(r"Repeat for confirmation: ")
    term.send("secret-2")
    term.expect(r"All done!")
    assert term.wait() == 0, term.output
    _, resp = _login(stack.web, "boss2", "secret-2")
    assert resp.status_code == 302

    # --- the database survived all of the above intact ----------------------
    check = stack.compose("exec", "-T", "web", "python", "-c", (
        "import sqlite3; c = sqlite3.connect('/app/instance/parts.db');"
        "print(c.execute('PRAGMA integrity_check').fetchone()[0], c.execute('PRAGMA journal_mode').fetchone()[0])"
    ))
    assert check.stdout.split() == ["ok", "delete"], check.stdout + check.stderr

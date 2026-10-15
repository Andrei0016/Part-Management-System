"""install.sh preflight checks, driven with a fake `docker` command so they
run anywhere without Docker. The full install against real Docker is in
test_install_e2e.py (opt-in)."""
import os
import shutil
import socket
import subprocess

import pytest

from conftest import REPO_ROOT

INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")

FAKE_DOCKER_OK = """#!/bin/sh
case "$1 $2" in
  "info "*) exit 0 ;;
  "compose version") echo "Docker Compose version v2"; exit 0 ;;
  "compose ps") exit 0 ;;
esac
echo "unexpected: docker $*" >&2
exit 99
"""


def _fake_bin(tmp_path, docker_script=None, downloader=True):
    """A bin dir with selected real tools plus (optionally) a fake docker,
    so tests don't depend on whether Docker is installed for real."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "sh", "uname", "dirname", "cat", "grep", "cut", "tail", "mkdir", "mktemp",
                 "rm", "mv", "cp", "chmod", "tr", "awk", "seq", "sleep", "env", "pwd", "head", "od", "find", "id",
                 *(("curl", "tar") if downloader else ())):
        real = shutil.which(tool)
        if real:
            os.symlink(real, bin_dir / tool)
    if docker_script is not None:
        docker = bin_dir / "docker"
        docker.write_text(docker_script)
        docker.chmod(0o755)
    return bin_dir


def _existing_install(tmp_path, env_lines=()):
    """A git-checkout-style install dir, so the script skips downloading."""
    install = tmp_path / "pms"
    (install / ".git").mkdir(parents=True)
    shutil.copy(INSTALL_SH, install / "install.sh")
    (install / "docker-compose.yml").write_text("services: {}\n")
    (install / "wsgi.py").write_text("")
    (install / ".env").write_text("SECRET_KEY=x\n" + "".join(f"{line}\n" for line in env_lines))
    return install


def run_install(bin_dir, cwd, script=None, extra_env=None):
    if script is None:
        # Never run the repo's own copy: next to docker-compose.yml it would
        # treat the repo itself as the install to update.
        fresh = cwd / "fresh"
        fresh.mkdir(exist_ok=True)
        script = shutil.copy(INSTALL_SH, fresh / "install.sh")
    env = {"PATH": str(bin_dir), "HOME": str(cwd), "TMPDIR": str(cwd), **(extra_env or {})}
    # start_new_session: no controlling terminal, so the script can't
    # prompt on (or read from) whatever terminal is running pytest.
    return subprocess.run(
        ["bash", str(script)], cwd=cwd, env=env, capture_output=True, text=True,
        timeout=60, start_new_session=True,
    )


def test_syntax():
    subprocess.run(["bash", "-n", INSTALL_SH], check=True)


def test_missing_docker(tmp_path):
    result = run_install(_fake_bin(tmp_path), tmp_path)
    assert result.returncode == 1
    assert "Docker is not installed" in result.stderr
    assert "How to fix" in result.stderr


@pytest.mark.parametrize("error,expected,fix", [
    ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
     "not running", "start"),
    ("permission denied while trying to connect to the Docker daemon socket",
     "isn't allowed to use Docker", "usermod -aG docker"),
])
def test_docker_daemon_problems(tmp_path, error, expected, fix):
    script = f'#!/bin/sh\nif [ "$1" = info ]; then echo "{error}" >&2; exit 1; fi\nexit 0\n'
    result = run_install(_fake_bin(tmp_path, script), tmp_path)
    assert result.returncode == 1
    assert expected in result.stderr
    assert fix.lower() in result.stderr.lower()


def test_missing_compose(tmp_path):
    script = '#!/bin/sh\n[ "$1" = info ] && exit 0\nexit 1\n'
    result = run_install(_fake_bin(tmp_path, script), tmp_path)
    assert result.returncode == 1
    assert "Docker Compose is not installed" in result.stderr


def test_missing_downloader(tmp_path):
    bin_dir = _fake_bin(tmp_path, FAKE_DOCKER_OK, downloader=False)
    result = run_install(bin_dir, tmp_path)
    assert result.returncode == 1
    assert "Neither curl nor wget" in result.stderr


def test_busy_port_reported_with_fix(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    busy = listener.getsockname()[1]
    try:
        install = _existing_install(tmp_path, [f"WEB_PORT={busy}", "MCP_PUBLIC_PORT=1"])
        result = run_install(_fake_bin(tmp_path, FAKE_DOCKER_OK), tmp_path, script=install / "install.sh")
    finally:
        listener.close()
    assert result.returncode == 1
    assert f"Port {busy} is already in use" in result.stderr
    assert "WEB_PORT / MCP_PUBLIC_PORT" in result.stderr


def test_update_never_rewrites_env(tmp_path):
    """Everything up to the build runs; the fake docker then fails the build,
    which must leave .env exactly as it was."""
    install = _existing_install(tmp_path, ["WEB_PORT=1", "MCP_PUBLIC_PORT=2", "SITE_TITLE=Mine"])
    before = (install / ".env").read_text()
    result = run_install(_fake_bin(tmp_path, FAKE_DOCKER_OK), tmp_path, script=install / "install.sh")
    assert "Updating the existing install" in result.stdout
    assert "Docker couldn't build or start PMS" in result.stderr
    assert (install / ".env").read_text() == before


FAKE_DOCKER_RUNNING = """#!/bin/sh
# A stack that's already up. Like the real thing, `compose exec` reads
# stdin, which is where a piped install script lives.
case "$1 $2" in
  "info "*) exit 0 ;;
  "compose version") exit 0 ;;
  "compose ps") echo "abc123"; exit 0 ;;
  "compose up") exit 0 ;;
  "compose exec") cat >/dev/null; echo 1; exit 0 ;;
esac
exit 0
"""


def test_piped_update_runs_to_the_end(tmp_path):
    """The documented install is `wget -qO- .../install.sh | bash`, where bash
    reads the script from stdin as it goes. Anything that reads stdin (like
    `docker compose exec`) mustn't swallow the rest of the script."""
    import functools
    import http.server
    import threading

    site = tmp_path / "site"
    site.mkdir()
    (site / "login").write_text("ok")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        install = _existing_install(tmp_path, [f"WEB_PORT={server.server_address[1]}", "MCP_PUBLIC_PORT=1"])
        bin_dir = _fake_bin(tmp_path, FAKE_DOCKER_RUNNING)
        result = subprocess.run(
            ["bash"], input=open(INSTALL_SH).read(), cwd=install, capture_output=True, text=True,
            env={"PATH": str(bin_dir), "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
            timeout=60, start_new_session=True,
        )
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PMS is running" in result.stdout
    assert "All done!" in result.stdout


def _downloaded_install(tmp_path, parent):
    """An install made by the installer (no .git), plus a tarball of a newer
    version that the update will try to copy over it."""
    import tarfile

    install = parent / "pms"
    install.mkdir(parents=True)
    shutil.copy(INSTALL_SH, install / "install.sh")
    for name, text in {"docker-compose.yml": "old\n", "wsgi.py": "old\n", "README.md": "old\n"}.items():
        (install / name).write_text(text)
    (install / ".env").write_text("SECRET_KEY=x\nWEB_PORT=1\nMCP_PUBLIC_PORT=2\n")

    new = tmp_path / "new" / "Part-Management-System-master"
    new.mkdir(parents=True)
    shutil.copy(INSTALL_SH, new / "install.sh")
    for name in ("docker-compose.yml", "wsgi.py", "README.md"):
        (new / name).write_text("new\n")
    tarball = tmp_path / "src.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(new, arcname=new.name)
    return install, {"PMS_TARBALL_URL": f"file://{tarball}"}


@pytest.fixture
def no_root():
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")


def test_update_works_when_parent_dir_is_not_writable(tmp_path, no_root):
    """Like /opt/pms: /opt belongs to root, but the update only needs pms/."""
    opt = tmp_path / "opt"
    install, env = _downloaded_install(tmp_path, opt)
    opt.chmod(0o555)
    try:
        result = run_install(_fake_bin(tmp_path, FAKE_DOCKER_OK), tmp_path, script=install / "install.sh",
                             extra_env=env)
    finally:
        opt.chmod(0o755)
    assert "Can't" not in result.stderr.split("Docker couldn't")[0], result.stderr
    assert "Code is up to date" in result.stdout
    assert (install / "wsgi.py").read_text() == "new\n"


def test_update_refuses_unwritable_install_dir(tmp_path, no_root):
    install, env = _downloaded_install(tmp_path, tmp_path / "opt")
    install.chmod(0o555)
    try:
        result = run_install(_fake_bin(tmp_path, FAKE_DOCKER_OK), tmp_path, script=install / "install.sh",
                             extra_env=env)
    finally:
        install.chmod(0o755)
    assert result.returncode == 1
    assert f"sudo chown -R" in result.stderr and str(install) in result.stderr
    assert "PMS_DIR" not in result.stderr  # a new folder would be a second, empty install


def test_update_checks_every_file_before_copying_any(tmp_path, no_root):
    install, env = _downloaded_install(tmp_path, tmp_path / "opt")
    (install / "wsgi.py").chmod(0o444)  # e.g. left behind by an earlier install run as root
    result = run_install(_fake_bin(tmp_path, FAKE_DOCKER_OK), tmp_path, script=install / "install.sh",
                         extra_env=env)
    assert result.returncode == 1
    assert "wsgi.py" in result.stderr and "sudo chown -R" in result.stderr
    # Nothing was half-copied.
    assert (install / "docker-compose.yml").read_text() == "old\n"
    assert (install / "README.md").read_text() == "old\n"

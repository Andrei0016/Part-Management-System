from app.models import ApiKey, User


def test_create_admin(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--username", "boss", "--full-name", "Boss", "--password", "pw"])
    assert result.exit_code == 0, result.output
    user = User.query.filter_by(username="boss").one()
    assert user.is_admin and user.role == "admin" and user.check_password("pw")


def test_create_admin_password_from_env(app):
    """The installer passes the password through the environment so it never
    shows up in a process listing."""
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin", "--username", "boss", "--full-name", "Boss"],
        env={"PMS_ADMIN_PASSWORD": "from-env"},
    )
    assert result.exit_code == 0, result.output
    assert User.query.filter_by(username="boss").one().check_password("from-env")


def test_create_admin_prompts(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin"], input="boss\nBoss\npw\npw\n")
    assert result.exit_code == 0, result.output
    assert User.query.filter_by(username="boss").one().is_admin


def test_create_admin_duplicate_fails(app, users):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--username", "admin", "--full-name", "A", "--password", "pw"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_create_api_key(app, users):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-api-key", "--label", "laptop", "--username", "member"])
    assert result.exit_code == 0, result.output
    raw = result.output.split(": ")[1].split()[0]
    assert len(raw) == 64
    assert ApiKey.query.one().owner.username == "member"
    assert app.test_client().get("/api/parts", headers={"Authorization": f"Bearer {raw}"}).status_code == 200


def test_create_api_key_unknown_user_fails(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-api-key", "--label", "x", "--username", "ghost"])
    assert result.exit_code != 0
    assert ApiKey.query.count() == 0

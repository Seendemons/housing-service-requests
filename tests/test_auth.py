import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import digest, hash_password, verify_password
from app.database import get_db
from app.main import app, settings
from app.models import AuthSession, Base, Role, User


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all(
            [
                User(
                    login="alice",
                    password_hash=hash_password("test-password"),
                    role=Role.RESIDENT,
                ),
                User(
                    login="bob",
                    password_hash=hash_password("test-password"),
                    role=Role.DISPATCHER,
                ),
                User(login="bad", password_hash="broken-hash", role=Role.RESIDENT),
            ]
        )

    def override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client, factory
    app.dependency_overrides.clear()
    engine.dispose()


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def test_password_verification():
    hashed = hash_password("test-password")
    assert verify_password("test-password", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", "bad hash")
    assert not verify_password("anything", "$argon2id$broken")


def test_login_failures_and_csrf(client):
    c, _ = client
    assert c.post("/login", data={}).status_code == 403
    page = c.get("/login")
    token = csrf(page)
    for invalid in ("", "x", "a" * 10000, "ä" * 43):
        assert c.post("/login", data={"csrf_token": invalid}).status_code == 403
    for name, password in [("nobody", "x"), ("bad", "x"), ("alice", "wrong")]:
        response = c.post(
            "/login", data={"csrf_token": token, "login": name, "password": password}
        )
        assert response.status_code == 401
        assert "Неверный логин или пароль" in response.text
    with TestClient(app) as other:
        other_page = other.get("/login")
        assert csrf(other_page) != token
        assert other.post("/login", data={"csrf_token": token}).status_code == 403
    repeated = c.get("/login")
    assert csrf(repeated) == token
    assert "set-cookie" not in repeated.headers


@pytest.mark.parametrize("name", ["\x00", "\x00alice", "al\x00ice", "alice\x00"])
def test_login_rejects_nul_before_database_query(
    client, reject_nul_sql_parameters, name
):
    c, factory = client
    page = c.get("/login")
    token = csrf(page)
    old_session = c.cookies["session"]
    response = c.post(
        "/login",
        data={"csrf_token": token, "login": name, "password": "test-password"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.text
    assert csrf(response) == token
    assert c.cookies["session"] == old_session
    with factory() as db:
        sessions = db.scalars(select(AuthSession)).all()
        assert len(sessions) == 1
        assert sessions[0].user_id is None
        assert sorted(db.scalars(select(User.login))) == ["alice", "bad", "bob"]


@pytest.mark.parametrize(
    "name, route, other",
    [("alice", "/resident", "/dispatcher"), ("bob", "/dispatcher", "/resident")],
)
def test_login_rotation_role_and_logout(client, name, route, other):
    c, factory = client
    assert c.get(route, follow_redirects=False).headers["location"] == "/login"
    response = c.get("/login")
    old = c.cookies["session"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert "max-age=28800" in response.headers["set-cookie"].lower()
    assert "path=/" in response.headers["set-cookie"].lower()
    assert "secure" not in response.headers["set-cookie"].lower()
    old_csrf = csrf(response)
    response = c.post(
        "/login",
        data={"csrf_token": old_csrf, "login": name, "password": "test-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == route
    current = c.cookies["session"]
    assert old != current
    with factory() as db:
        assert (
            db.scalar(
                select(AuthSession).where(AuthSession.token_digest == digest(old))
            )
            is None
        )
        assert (
            db.scalar(
                select(AuthSession).where(AuthSession.token_digest == digest(current))
            )
            is not None
        )
    assert c.get(other).status_code == 403
    login_page = c.get("/login", follow_redirects=False)
    assert login_page.status_code == 303
    assert login_page.headers["location"] == route
    assert "set-cookie" not in login_page.headers
    assert c.cookies["session"] == current
    assert c.post("/logout", data={"csrf_token": old_csrf}).status_code == 403
    account = c.get(route)
    assert name in account.text
    assert c.post("/logout", data={}).status_code == 403
    assert c.post("/logout", data={"csrf_token": "bad"}).status_code == 403
    assert c.get("/logout").status_code == 405
    response = c.post(
        "/logout", data={"csrf_token": csrf(account)}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "max-age=0" in response.headers["set-cookie"].lower()
    c.cookies.set("session", current)
    assert c.get(route, follow_redirects=False).headers["location"] == "/login"
    c.cookies.set("session", old)
    assert c.get(route, follow_redirects=False).headers["location"] == "/login"


def test_expired_and_forged_sessions(client):
    c, factory = client
    page = c.get("/login")
    assert (
        c.post(
            "/login",
            data={
                "csrf_token": csrf(page),
                "login": "alice",
                "password": "test-password",
            },
            follow_redirects=False,
        ).status_code
        == 303
    )
    token = c.cookies["session"]
    with factory.begin() as db:
        row = db.scalar(
            select(AuthSession).where(AuthSession.token_digest == digest(token))
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert c.get("/resident", follow_redirects=False).headers["location"] == "/login"
    for forged in ("a" * 43, "a" * 10000):
        c.cookies.clear()
        c.cookies.set("session", forged)
        assert (
            c.get("/resident", follow_redirects=False).headers["location"] == "/login"
        )
        assert c.post("/login", data={"csrf_token": csrf(page)}).status_code == 403


def test_role_is_read_from_database(client):
    c, factory = client
    page = c.get("/login")
    c.post(
        "/login",
        data={"csrf_token": csrf(page), "login": "alice", "password": "test-password"},
        follow_redirects=False,
    )
    assert c.get("/resident").status_code == 200
    with factory.begin() as db:
        db.scalar(select(User).where(User.login == "alice")).role = Role.DISPATCHER
    assert c.get("/resident").status_code == 403
    assert c.get("/dispatcher").status_code == 200


def test_secure_cookie_over_https(client, monkeypatch):
    _, _factory = client
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    with TestClient(app, base_url="https://testserver") as https:
        page = https.get("/login")
        assert "; secure" in page.headers["set-cookie"].lower()
        response = https.post(
            "/login",
            data={
                "csrf_token": csrf(page),
                "login": "alice",
                "password": "test-password",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "; secure" in response.headers["set-cookie"].lower()
        account = https.get("/resident")
        assert account.status_code == 200
        assert (
            https.post(
                "/logout", data={"csrf_token": csrf(account)}, follow_redirects=False
            ).status_code
            == 303
        )

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_session, digest, hash_password
from app.database import get_db
from app.main import app
from app.models import (
    AuthSession,
    Base,
    Executor,
    RequestHistory,
    RequestStatus,
    Role,
    ServiceRequest,
    User,
)


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        password = hash_password("test-password")
        db.add_all(
            [
                User(id=1, login="alice", role=Role.RESIDENT, password_hash=password),
                User(id=2, login="eve", role=Role.RESIDENT, password_hash=password),
                User(id=3, login="bob", role=Role.DISPATCHER, password_hash=password),
                Executor(
                    id=1, name="Сантехник <script>x</script>", specialization="Вода"
                ),
                Executor(id=2, name="Электрик", specialization="<b>Свет</b>"),
            ]
        )
        db.flush()
        db.add(
            ServiceRequest(
                id=10,
                resident_id=1,
                location="Лифт",
                description="Не работает",
                status=RequestStatus.NEW,
                created_at=datetime(2026, 11, 1, tzinfo=UTC),
                updated_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
        )

    def override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as c:
            yield c, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def sign_in(c, factory, user_id=3):
    with factory.begin() as db:
        session, token = create_session(db, db.get(User, user_id) if user_id else None)
        csrf = session.csrf_token
    c.cookies.clear()
    c.cookies.set("session", token)
    return csrf, token


def snapshot(factory):
    with factory() as db:
        r = db.get(ServiceRequest, 10)
        return (
            r.status,
            r.executor_id,
            r.updated_at,
            r.created_at,
            r.resident_id,
            r.location,
            r.description,
            r.closing_comment,
            [(h.actor_id, h.comment) for h in db.scalars(select(RequestHistory))],
        )


def test_assignment_form_and_empty_directory(client):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    before = snapshot(factory)
    page = c.get("/dispatcher/requests/10")
    assert page.status_code == 200
    assert 'method="post" action="/dispatcher/requests/10/start"' in page.text
    assert f'name="csrf_token" value="{csrf}"' in page.text
    assert '<label for="executor_id">' in page.text
    assert '<select id="executor_id" name="executor_id" required>' in page.text
    assert "&lt;script&gt;x&lt;/script&gt;" in page.text
    assert "&lt;b&gt;Свет&lt;/b&gt;" in page.text
    assert "<script>" not in page.text
    assert snapshot(factory) == before
    with factory.begin() as db:
        for executor in db.scalars(select(Executor)):
            db.delete(executor)
    page = c.get("/dispatcher/requests/10")
    assert "В справочнике пока нет исполнителей." in page.text
    assert "/start" not in page.text
    response = c.post(
        "/dispatcher/requests/10/start", data={"csrf_token": csrf, "executor_id": "1"}
    )
    assert response.status_code == 422
    assert snapshot(factory) == before


def test_start_persists_executor_status_history_and_resident_sees_it(client):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    before = snapshot(factory)
    response = c.post(
        "/dispatcher/requests/10/start",
        data={
            "csrf_token": csrf,
            "executor_id": "1",
            "status": "closed",
            "resident_id": "2",
            "actor_id": "2",
            "closing_comment": "Injected",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dispatcher/requests/10"
    after = snapshot(factory)
    assert after[0:2] == (RequestStatus.IN_PROGRESS, 1)
    assert after[2] > before[2] and after[3:8] == before[3:8]
    with factory() as db:
        history = db.scalars(select(RequestHistory)).one()
        assert history.request_id == 10 and history.actor_id == 3
        assert history.previous_status == RequestStatus.NEW
        assert history.new_status == RequestStatus.IN_PROGRESS
        assert history.created_at == after[2]
        assert history.comment == "Назначен исполнитель: Сантехник <script>x</script>."
    page = c.get("/dispatcher/requests/10")
    assert "Новая → В работе" in page.text and "/start" not in page.text
    assert 'href="/dispatcher/requests/10"' not in c.get("/dispatcher?status=new").text
    assert (
        'href="/dispatcher/requests/10"' in c.get("/dispatcher?status=in_progress").text
    )
    sign_in(c, factory, 1)
    page = c.get("/resident/requests/10")
    assert "Новая → В работе" in page.text and "bob" in page.text
    assert "Сантехник &lt;script&gt;x&lt;/script&gt; · Вода" in page.text
    assert "<script>" not in page.text and "/start" not in page.text
    sign_in(c, factory, 2)
    assert c.get("/resident/requests/10").status_code == 404
    assert snapshot(factory) == after


@pytest.mark.parametrize(
    "executor_id",
    [
        "",
        "0",
        "-1",
        "999",
        "abc",
        "1.0",
        "1' OR 1=1",
        "\x00",
        "１",
        "2147483648",
        "9" * 5000,
    ],
)
def test_invalid_executor_does_not_change_request(
    client, executor_id, reject_nul_sql_parameters
):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    before = snapshot(factory)
    response = c.post(
        "/dispatcher/requests/10/start",
        data={"csrf_token": csrf, "executor_id": executor_id},
    )
    assert response.status_code == 422
    assert "Выберите существующего исполнителя из справочника." in response.text
    assert 'aria-invalid="true" aria-describedby="assignment-error"' in response.text
    assert 'action="/dispatcher/requests/10/start"' in response.text
    assert snapshot(factory) == before


@pytest.mark.parametrize(
    "status", [RequestStatus.DRAFT, RequestStatus.IN_PROGRESS, RequestStatus.CLOSED]
)
def test_wrong_status_cannot_assign_or_reassign(client, status):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    with factory.begin() as db:
        r = db.get(ServiceRequest, 10)
        r.status = status
        r.executor_id = 2 if status != RequestStatus.DRAFT else None
    before = snapshot(factory)
    response = c.post(
        "/dispatcher/requests/10/start", data={"csrf_token": csrf, "executor_id": "1"}
    )
    assert response.status_code == (404 if status == RequestStatus.DRAFT else 409)
    assert snapshot(factory) == before
    page = c.get("/dispatcher/requests/10")
    assert "/start" not in page.text


def test_missing_request(client):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    before = snapshot(factory)
    response = c.post(
        "/dispatcher/requests/999/start", data={"csrf_token": csrf, "executor_id": "1"}
    )
    assert response.status_code == 404 and response.json() == {
        "detail": "Заявка не найдена"
    }
    assert snapshot(factory) == before


@pytest.mark.parametrize("csrf", [None, "", "wrong"])
def test_csrf_checked_before_write(client, csrf):
    c, factory = client
    sign_in(c, factory)
    before = snapshot(factory)
    data = {"executor_id": "1"}
    if csrf is not None:
        data["csrf_token"] = csrf
    assert c.post("/dispatcher/requests/10/start", data=data).status_code == 403
    assert snapshot(factory) == before


@pytest.mark.parametrize(
    "identity", ["anonymous", "guest", "expired", "forged", "resident"]
)
def test_role_and_session_checked_before_write(client, identity):
    c, factory = client
    csrf = ""
    if identity in {"guest", "expired", "resident"}:
        csrf, token = sign_in(
            c,
            factory,
            None if identity == "guest" else 1 if identity == "resident" else 3,
        )
        if identity == "expired":
            with factory.begin() as db:
                db.scalar(
                    select(AuthSession).where(AuthSession.token_digest == digest(token))
                ).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif identity == "forged":
        c.cookies.set("session", "a" * 43)
    before = snapshot(factory)
    response = c.post(
        "/dispatcher/requests/10/start",
        data={"csrf_token": csrf, "executor_id": "1"},
        follow_redirects=False,
    )
    assert response.status_code == (403 if identity == "resident" else 303)
    if identity != "resident":
        assert response.headers["location"] == "/login"
    assert snapshot(factory) == before


def test_repeated_start_does_not_reassign_or_duplicate_history(client):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    assert (
        c.post(
            "/dispatcher/requests/10/start",
            data={"csrf_token": csrf, "executor_id": "1"},
        ).status_code
        == 200
    )
    before = snapshot(factory)
    assert (
        c.post(
            "/dispatcher/requests/10/start",
            data={"csrf_token": csrf, "executor_id": "2"},
        ).status_code
        == 409
    )
    assert snapshot(factory) == before


def test_status_change_between_read_and_update_cannot_overwrite_assignment(
    client, monkeypatch
):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    from app import main as module

    original = module.get_dispatcher_request

    def start_after_read(db, request_id):
        r = original(db, request_id)
        # Имитируем завершившуюся конкурирующую операцию после SELECT.
        with factory.begin() as other:
            other.execute(
                update(ServiceRequest)
                .where(ServiceRequest.id == request_id)
                .values(status=RequestStatus.IN_PROGRESS, executor_id=2)
            )
            other.add(
                RequestHistory(
                    request_id=request_id,
                    actor_id=3,
                    previous_status=RequestStatus.NEW,
                    new_status=RequestStatus.IN_PROGRESS,
                    comment="Другая операция",
                )
            )
        return r

    monkeypatch.setattr(module, "get_dispatcher_request", start_after_read)
    response = c.post(
        "/dispatcher/requests/10/start", data={"csrf_token": csrf, "executor_id": "1"}
    )
    assert response.status_code == 409
    with factory() as db:
        assert db.get(ServiceRequest, 10).executor_id == 2
        assert [h.comment for h in db.scalars(select(RequestHistory))] == [
            "Другая операция"
        ]


@pytest.mark.parametrize("failure_point", ["before_insert", "after_insert"])
def test_assignment_and_history_roll_back_together(client, monkeypatch, failure_point):
    c, factory = client
    csrf, _ = sign_in(c, factory)
    before = snapshot(factory)
    app.dependency_overrides.pop(get_db)
    monkeypatch.setattr("app.database.get_session_factory", lambda: factory)

    def fail(mapper, connection, target):
        row = connection.execute(
            select(ServiceRequest.status, ServiceRequest.executor_id).where(
                ServiceRequest.id == 10
            )
        ).one()
        assert row == (RequestStatus.IN_PROGRESS, 1)
        raise RuntimeError("history write failed")

    event.listen(RequestHistory, failure_point, fail)
    try:
        with pytest.raises(RuntimeError, match="history write failed"):
            c.post(
                "/dispatcher/requests/10/start",
                data={"csrf_token": csrf, "executor_id": "1"},
            )
    finally:
        event.remove(RequestHistory, failure_point, fail)
    assert snapshot(factory) == before
    assert (
        c.post(
            "/dispatcher/requests/10/start",
            data={"csrf_token": csrf, "executor_id": "1"},
        ).status_code
        == 200
    )


def test_real_login_create_submit_assign_and_read_after_login(client):
    c, factory = client

    def login_as(name):
        c.cookies.clear()
        csrf = re.search(
            r'name="csrf_token" value="([^"]+)"', c.get("/login").text
        ).group(1)
        response = c.post(
            "/login",
            data={"csrf_token": csrf, "login": name, "password": "test-password"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        return re.search(
            r'name="csrf_token" value="([^"]+)"',
            c.get(response.headers["location"]).text,
        ).group(1)

    csrf = login_as("alice")
    created = c.post(
        "/resident/requests",
        data={
            "csrf_token": csrf,
            "location": "Подъезд",
            "description": "Сломана лампа",
        },
        follow_redirects=False,
    )
    request_id = int(created.headers["location"].rsplit("/", 1)[1])
    assert (
        c.post(
            f"/resident/requests/{request_id}/submit", data={"csrf_token": csrf}
        ).status_code
        == 200
    )
    csrf = login_as("bob")
    assert (
        c.post(
            f"/dispatcher/requests/{request_id}/start",
            data={"csrf_token": csrf, "executor_id": "2"},
        ).status_code
        == 200
    )
    login_as("bob")
    assert (
        "Электрик · &lt;b&gt;Свет&lt;/b&gt;"
        in c.get(f"/dispatcher/requests/{request_id}").text
    )
    login_as("alice")
    page = c.get(f"/resident/requests/{request_id}")
    assert "Черновик → Новая" in page.text and "Новая → В работе" in page.text
    assert "Электрик · &lt;b&gt;Свет&lt;/b&gt;" in page.text
    assert "/start" not in page.text

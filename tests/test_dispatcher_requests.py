import re
from datetime import UTC, datetime, timedelta
from html import escape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_session, digest, hash_password
from app.database import get_db
from app.main import app
from app.models import (
    AuthSession,
    Base,
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
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        password_hash = hash_password("test-password")
        db.add_all(
            [
                User(
                    id=1, login="alice", password_hash=password_hash, role=Role.RESIDENT
                ),
                User(
                    id=2, login="eve", password_hash=password_hash, role=Role.RESIDENT
                ),
                User(
                    id=3, login="bob", password_hash=password_hash, role=Role.DISPATCHER
                ),
            ]
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
        _, token = create_session(db, db.get(User, user_id) if user_id else None)
    c.cookies.clear()
    c.cookies.set("session", token)
    return token


@pytest.fixture
def requests(client):
    _, factory = client
    with factory.begin() as db:
        for i, status in enumerate(RequestStatus, start=1):
            db.add(
                ServiceRequest(
                    id=i,
                    resident_id=1 if i % 2 else 2,
                    location=f"Место {i} <script>location</script>",
                    description=f"Описание {i}\n<script>description</script>",
                    status=status,
                    created_at=datetime(2026, 11, 9 if i > 2 else 8, tzinfo=UTC),
                )
            )
    return [1, 2, 3, 4]


def test_list_all_residents_sent_only_order_and_safe_content(client, requests):
    c, factory = client
    sign_in(c, factory)
    page = c.get("/dispatcher")
    assert page.status_code == 200
    assert [row.id for row in page.context["service_requests"]] == [4, 3, 2]
    for i in [2, 3, 4]:
        assert f'href="/dispatcher/requests/{i}"' in page.text
        assert escape(f"Место {i} <script>location</script>") in page.text
    assert 'href="/dispatcher/requests/1"' not in page.text
    assert "Место 1" not in page.text
    assert "<script>" not in page.text
    assert "Новая" in page.text and "В работе" in page.text and "Закрыта" in page.text
    assert 'method="get" action="/dispatcher"' in page.text
    assert '<label for="status">' in page.text
    assert '<select id="status" name="status">' in page.text
    assert 'value="draft"' not in page.text
    assert 'method="post" action="/logout"' in page.text
    assert (
        'href="/static/styles.css"' in page.text or '/static/styles.css"' in page.text
    )
    assert 'href="/resident' not in page.text


@pytest.mark.parametrize(
    "status,ids", [("", [4, 3, 2]), ("new", [2]), ("in_progress", [3]), ("closed", [4])]
)
def test_filter_status_and_reset(client, requests, status, ids):
    c, factory = client
    sign_in(c, factory)
    page = c.get("/dispatcher", params={"status": status})
    assert page.status_code == 200
    assert [row.id for row in page.context["service_requests"]] == ids
    assert f'<option value="{status}" selected>' in page.text
    assert '<a href="/dispatcher">Сбросить фильтр</a>' in page.text
    reset = c.get("/dispatcher")
    assert [row.id for row in reset.context["service_requests"]] == [4, 3, 2]


@pytest.mark.parametrize(
    "status", ["draft", "unknown", "NEW", "<script>", "new' OR 1=1", "\x00"]
)
def test_invalid_filter_cannot_expose_drafts(
    client, requests, status, reject_nul_sql_parameters
):
    c, factory = client
    sign_in(c, factory)
    response = c.get("/dispatcher", params={"status": status})
    assert response.status_code == 422
    assert response.json()["detail"] == "Недопустимый фильтр статуса"
    assert "Место 1" not in response.text


def test_empty_list_drafts_only_and_empty_filter(client):
    c, factory = client
    sign_in(c, factory)
    assert "Отправленных заявок пока нет." in c.get("/dispatcher").text
    with factory.begin() as db:
        db.add(ServiceRequest(resident_id=1, location="Секрет", description="Черновик"))
        db.add(
            ServiceRequest(
                resident_id=2,
                location="Лифт",
                description="Не работает",
                status=RequestStatus.NEW,
            )
        )
    page = c.get("/dispatcher", params={"status": "closed"})
    assert page.status_code == 200 and page.context["service_requests"] == []
    assert "Заявок с выбранным статусом пока нет." in page.text
    with factory.begin() as db:
        db.delete(
            db.scalar(
                select(ServiceRequest).where(ServiceRequest.status == RequestStatus.NEW)
            )
        )
    page = c.get("/dispatcher")
    assert "Отправленных заявок пока нет." in page.text and "Секрет" not in page.text


@pytest.mark.parametrize(
    "request_id,label,resident",
    [(2, "Новая", "eve"), (3, "В работе", "alice"), (4, "Закрыта", "eve")],
)
def test_sent_cards_show_details_without_edit_or_submit(
    client, requests, request_id, label, resident
):
    c, factory = client
    sign_in(c, factory)
    page = c.get(f"/dispatcher/requests/{request_id}")
    assert page.status_code == 200
    assert f"Заявка № {request_id}" in page.text and label in page.text
    assert resident in page.text and "Роль: диспетчер" in page.text
    assert escape(f"Описание {request_id}\n<script>description</script>") in page.text
    assert "UTC" in page.text and "История пока пуста." in page.text
    assert '<a href="/dispatcher">К списку заявок</a>' in page.text
    assert "<script>" not in page.text
    assert "/edit" not in page.text and "/submit" not in page.text
    assert 'href="/resident' not in page.text


@pytest.mark.parametrize("request_id", [1, 99999])
def test_draft_and_missing_card_have_same_404(client, requests, request_id):
    c, factory = client
    sign_in(c, factory)
    page = c.get(f"/dispatcher/requests/{request_id}")
    assert page.status_code == 404
    assert page.json() == {"detail": "Заявка не найдена"}


@pytest.mark.parametrize(
    "identity", ["anonymous", "guest", "expired", "forged", "resident"]
)
def test_access_checked_for_list_filter_and_cards(client, requests, identity):
    c, factory = client
    if identity in {"guest", "expired", "resident"}:
        token = sign_in(
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
    for url in [
        "/dispatcher",
        "/dispatcher?status=new",
        "/dispatcher?status=draft",
        "/dispatcher/requests/2",
        "/dispatcher/requests/1",
        "/dispatcher/requests/99999",
    ]:
        page = c.get(url, follow_redirects=False)
        assert page.status_code == (403 if identity == "resident" else 303)
        if identity != "resident":
            assert page.headers["location"] == "/login"
        assert "Место" not in page.text


def test_history_sorted_scoped_escaped_and_views_do_not_write(client, requests):
    c, factory = client
    sign_in(c, factory)
    at = datetime(2026, 11, 9, 12, tzinfo=UTC)
    with factory.begin() as db:
        db.get(User, 2).login = "<script>resident</script>"
        db.get(User, 3).login = "<script>actor</script>"
        db.add_all(
            [
                RequestHistory(
                    id=1,
                    request_id=2,
                    actor_id=3,
                    previous_status=RequestStatus.NEW,
                    new_status=RequestStatus.IN_PROGRESS,
                    comment="second <script>comment</script>",
                    created_at=at,
                ),
                RequestHistory(
                    id=2,
                    request_id=2,
                    actor_id=2,
                    previous_status=RequestStatus.DRAFT,
                    new_status=RequestStatus.NEW,
                    comment="first",
                    created_at=at - timedelta(hours=1),
                ),
                RequestHistory(
                    id=3,
                    request_id=2,
                    actor_id=3,
                    previous_status=RequestStatus.IN_PROGRESS,
                    new_status=RequestStatus.CLOSED,
                    comment="third",
                    created_at=at,
                ),
                RequestHistory(
                    id=4,
                    request_id=3,
                    actor_id=1,
                    new_status=RequestStatus.NEW,
                    comment="other-history",
                    created_at=at,
                ),
            ]
        )
    with factory() as db:
        before = [
            (r.id, r.status, r.updated_at, r.location, r.description)
            for r in db.scalars(select(ServiceRequest).order_by(ServiceRequest.id))
        ]
    for _ in range(2):
        c.get("/dispatcher?status=new")
        page = c.get("/dispatcher/requests/2")
        assert [h.id for h in page.context["history"]] == [2, 1, 3]
        assert "09.11.2026 11:00 UTC" in page.text
        assert "Черновик → Новая" in page.text and "В работе → Закрыта" in page.text
        assert "&lt;script&gt;resident&lt;/script&gt;" in page.text
        assert "&lt;script&gt;actor&lt;/script&gt;" in page.text
        assert "second &lt;script&gt;comment&lt;/script&gt;" in page.text
        assert "other-history" not in page.text and "<script>" not in page.text
    with factory() as db:
        after = [
            (r.id, r.status, r.updated_at, r.location, r.description)
            for r in db.scalars(select(ServiceRequest).order_by(ServiceRequest.id))
        ]
        assert after == before
        assert len(db.scalars(select(RequestHistory)).all()) == 4


def test_real_login_submission_visible_to_dispatcher_after_new_login(client):
    c, factory = client

    def login_as(name):
        c.cookies.clear()
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', c.get("/login").text
        ).group(1)
        response = c.post(
            "/login",
            data={"csrf_token": token, "login": name, "password": "test-password"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        return re.search(
            r'name="csrf_token" value="([^"]+)"',
            c.get(response.headers["location"]).text,
        ).group(1)

    token = login_as("alice")
    created = c.post(
        "/resident/requests",
        data={"csrf_token": token, "location": "Лифт", "description": "Не работает"},
        follow_redirects=False,
    )
    request_id = int(created.headers["location"].rsplit("/", 1)[1])
    login_as("bob")
    assert "Отправленных заявок пока нет." in c.get("/dispatcher").text
    assert c.get(f"/dispatcher/requests/{request_id}").status_code == 404
    token = login_as("alice")
    assert (
        c.post(
            f"/resident/requests/{request_id}/submit",
            data={"csrf_token": token},
            follow_redirects=False,
        ).status_code
        == 303
    )
    login_as("bob")
    assert (
        f'href="/dispatcher/requests/{request_id}"'
        in c.get("/dispatcher?status=new").text
    )
    page = c.get(f"/dispatcher/requests/{request_id}")
    assert page.status_code == 200
    assert "alice" in page.text and "Черновик → Новая" in page.text
    login_as("bob")
    assert c.get(f"/dispatcher/requests/{request_id}").status_code == 200

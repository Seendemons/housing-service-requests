import re
from datetime import UTC, datetime, timedelta
from html import escape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_session, digest
from app.database import get_db
from app.main import app
from app.models import AuthSession, Base, RequestStatus, Role, ServiceRequest, User


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
                User(id=1, login="alice", password_hash="unused", role=Role.RESIDENT),
                User(id=2, login="eve", password_hash="unused", role=Role.RESIDENT),
                User(id=3, login="bob", password_hash="unused", role=Role.DISPATCHER),
            ]
        )

    def override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as test_client:
            yield test_client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def sign_in(c, factory, user_id=1):
    with factory.begin() as db:
        user = db.get(User, user_id) if user_id is not None else None
        session, token = create_session(db, user)
        csrf_token = session.csrf_token
    c.cookies.clear()
    c.cookies.set("session", token)
    return csrf_token


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def draft_count(factory):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(ServiceRequest))


def test_create_persists_trimmed_draft_and_ignores_privileged_fields(client):
    c, factory = client
    token = sign_in(c, factory)
    response = c.post(
        "/resident/requests",
        data={
            "csrf_token": token,
            "location": "  Подъезд 1  ",
            "description": "\nТечёт труба\t",
            "resident_id": "2",
            "status": "closed",
            "executor_id": "123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with factory() as db:
        saved = db.scalar(select(ServiceRequest))
        assert saved.resident_id == 1
        assert saved.status == RequestStatus.DRAFT
        assert saved.executor_id is None
        assert saved.location == "Подъезд 1"
        assert saved.description == "Течёт труба"
        detail_url = f"/resident/requests/{saved.id}"
    assert response.headers["location"] == detail_url
    detail = c.get(detail_url)
    assert detail.status_code == 200
    assert "Подъезд 1" in detail.text and "Течёт труба" in detail.text
    assert csrf(detail) == token
    assert detail.context["service_request"].resident_id == 1


def test_empty_list_and_form_get_do_not_create_requests(client):
    c, factory = client
    token = sign_in(c, factory)
    page = c.get("/resident")
    assert page.status_code == 200
    assert page.context["drafts"] == []
    assert "/resident/requests/new" in page.text
    for _ in range(2):
        form = c.get("/resident/requests/new")
        assert form.status_code == 200
        assert csrf(form) == token
        assert form.context["location"] == form.context["description"] == ""
        assert form.context["errors"] == {}
    assert draft_count(factory) == 0


def test_list_only_own_drafts_newest_first_and_detail_access(client):
    c, factory = client
    sign_in(c, factory)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later = earlier + timedelta(days=1)
    with factory.begin() as db:
        for row_id, owner, status, created in [
            (1, 1, RequestStatus.DRAFT, earlier),
            (2, 1, RequestStatus.DRAFT, later),
            (3, 1, RequestStatus.DRAFT, later),
            (4, 2, RequestStatus.DRAFT, later),
            (5, 1, RequestStatus.NEW, later),
            (6, 1, RequestStatus.IN_PROGRESS, later),
            (7, 1, RequestStatus.CLOSED, later),
        ]:
            db.add(
                ServiceRequest(
                    id=row_id,
                    resident_id=owner,
                    location=f"Место {row_id}",
                    description=f"Описание {row_id}",
                    status=status,
                    created_at=created,
                )
            )
    page = c.get("/resident")
    assert page.status_code == 200
    assert [row.id for row in page.context["drafts"]] == [3, 2, 1]
    assert page.text.index("Место 3") < page.text.index("Место 2")
    assert page.text.index("Место 2") < page.text.index("Место 1")
    for row_id in (1, 2, 3):
        assert f"/resident/requests/{row_id}" in page.text
        assert c.get(f"/resident/requests/{row_id}").status_code == 200
    for row_id in (4, 5, 6, 7, 999):
        assert f"Место {row_id}" not in page.text
        assert c.get(f"/resident/requests/{row_id}").status_code == 404


@pytest.mark.parametrize("identity", ["anonymous", "guest", "expired", "dispatcher"])
def test_all_routes_require_active_resident_session(client, identity):
    c, factory = client
    token = ""
    if identity != "anonymous":
        token = sign_in(
            c, factory, {"guest": None, "expired": 1, "dispatcher": 3}[identity]
        )
    if identity == "expired":
        with factory.begin() as db:
            db.scalar(
                select(AuthSession).where(
                    AuthSession.token_digest == digest(c.cookies["session"])
                )
            ).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    for method, route in [
        ("GET", "/resident"),
        ("GET", "/resident/requests/new"),
        ("GET", "/resident/requests/1"),
        ("POST", "/resident/requests"),
    ]:
        response = c.request(
            method,
            route,
            data={"csrf_token": token, "location": "Место", "description": "Течь"},
            follow_redirects=False,
        )
        assert response.status_code == (403 if identity == "dispatcher" else 303)
        if identity != "dispatcher":
            assert response.headers["location"] == "/login"
    assert draft_count(factory) == 0


def test_creation_rejects_missing_invalid_and_other_session_csrf(client):
    c, factory = client
    token = sign_in(c, factory)
    with TestClient(app) as other:
        other_token = sign_in(other, factory, 2)
    for invalid in (None, "", "invalid", "ä" * 43, other_token):
        data = {"location": "Место", "description": "Течь"}
        if invalid is not None:
            data["csrf_token"] = invalid
        assert c.post("/resident/requests", data=data).status_code == 403
    assert draft_count(factory) == 0
    assert csrf(c.get("/resident/requests/new")) == token


@pytest.mark.parametrize(
    "location,description,error_fields",
    [
        ("", "", {"location", "description"}),
        (" \t\n", " \n\t", {"location", "description"}),
        ("я" * 256, "Описание", {"location"}),
        ("Место", "я" * 10001, {"description"}),
        ("\x00", "Описание", {"location"}),
        ("\x00Место", "Описание", {"location"}),
        ("Ме\x00сто", "Описание", {"location"}),
        ("Место\x00", "Описание", {"location"}),
        ("Место", "\x00", {"description"}),
        ("Место", "\x00Описание", {"description"}),
        ("Место", "Опи\x00сание", {"description"}),
        ("Место", "Описание\x00", {"description"}),
        ("Место\x00", "Описание\x00", {"location", "description"}),
    ],
)
def test_invalid_form_preserves_values_and_does_not_save(
    client, reject_nul_sql_parameters, location, description, error_fields
):
    c, factory = client
    token = sign_in(c, factory)
    response = c.post(
        "/resident/requests",
        data={"csrf_token": token, "location": location, "description": description},
    )
    assert response.status_code == 422
    assert response.context["location"] == location
    assert response.context["description"] == description
    assert set(response.context["errors"]) == error_fields
    for message in response.context["errors"].values():
        assert message in response.text
    for field, value in (("location", location), ("description", description)):
        if "\x00" in value:
            assert "нулевой символ" in response.context["errors"][field]
    assert csrf(response) == token
    assert draft_count(factory) == 0


@pytest.mark.parametrize("location,description", [("я", "я"), ("я" * 255, "я" * 10000)])
def test_valid_boundaries_count_characters_after_trimming(
    client, location, description
):
    c, factory = client
    token = sign_in(c, factory)
    response = c.post(
        "/resident/requests",
        data={
            "csrf_token": token,
            "location": f" {location}\n",
            "description": f"\t{description} ",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with factory() as db:
        row = db.scalar(select(ServiceRequest))
        assert row.location == location and row.description == description


def test_form_and_saved_data_escape_html(client):
    c, factory = client
    token = sign_in(c, factory)
    location = '<script>alert("location")</script>'
    description = '</textarea><script>alert("description")</script>'
    invalid = c.post(
        "/resident/requests",
        data={"csrf_token": token, "location": location, "description": ""},
    )
    assert invalid.status_code == 422
    assert location not in invalid.text
    assert "&lt;script&gt;" in invalid.text
    invalid_description = c.post(
        "/resident/requests",
        data={"csrf_token": token, "location": "", "description": description},
    )
    assert invalid_description.status_code == 422
    assert description not in invalid_description.text
    assert "&lt;/textarea&gt;" in invalid_description.text
    created = c.post(
        "/resident/requests",
        data={"csrf_token": token, "location": location, "description": description},
        follow_redirects=False,
    )
    assert created.status_code == 303
    for page in (c.get("/resident"), c.get(created.headers["location"])):
        assert page.status_code == 200
        assert location not in page.text and description not in page.text
        # Jinja escapes quotes as numeric entities, so check the tag boundaries.
        assert escape("<script>") in page.text
    with factory() as db:
        row = db.scalar(select(ServiceRequest))
        assert row.location == location and row.description == description

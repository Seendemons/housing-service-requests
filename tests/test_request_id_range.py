import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import COOKIE_NAME, create_session
from app.database import get_db
from app.main import app
from app.models import Base, Role, User

REQUEST_ROUTES = [
    (Role.RESIDENT, "GET", "/resident/requests/{request_id}"),
    (Role.RESIDENT, "GET", "/resident/requests/{request_id}/edit"),
    (Role.RESIDENT, "POST", "/resident/requests/{request_id}/edit"),
    (Role.RESIDENT, "POST", "/resident/requests/{request_id}/submit"),
    (Role.DISPATCHER, "GET", "/dispatcher/requests/{request_id}"),
    (Role.DISPATCHER, "POST", "/dispatcher/requests/{request_id}/start"),
]


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    credentials = {}
    with factory.begin() as db:
        for role in Role:
            user = User(login=role.value, role=role, password_hash="unused")
            db.add(user)
            session, token = create_session(db, user)
            credentials[role] = token, session.csrf_token

    def override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as c:
            yield c, engine, credentials
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def request_as(client, role, method, path):
    c, _, credentials = client
    token, csrf = credentials[role]
    c.cookies.clear()
    c.cookies.set(COOKIE_NAME, token)
    return c.request(
        method,
        path,
        data={"csrf_token": csrf, "location": "Лифт", "description": "Не работает"}
        if method == "POST"
        else None,
        follow_redirects=False,
    )


@pytest.mark.parametrize("role,method,path", REQUEST_ROUTES)
@pytest.mark.parametrize(
    "request_id", ["0", "-1", "-2147483649", "2147483648", "9" * 100, "9" * 5000]
)
def test_invalid_request_id_rejected_before_sql(client, role, method, path, request_id):
    _, engine, _ = client

    def reject_sql(*args):
        pytest.fail("Некорректный номер заявки не должен приводить к SQL")

    event.listen(engine, "before_cursor_execute", reject_sql)
    try:
        response = request_as(client, role, method, path.format(request_id=request_id))
    finally:
        event.remove(engine, "before_cursor_execute", reject_sql)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "request_id"]


@pytest.mark.parametrize("role,method,path", REQUEST_ROUTES)
@pytest.mark.parametrize("request_id", [1, 2147483647])
def test_valid_missing_request_id_still_returns_404(
    client, role, method, path, request_id
):
    response = request_as(client, role, method, path.format(request_id=request_id))
    assert response.status_code == 404

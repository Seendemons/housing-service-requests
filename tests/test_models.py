from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Executor,
    RequestHistory,
    RequestStatus,
    Role,
    ServiceRequest,
    User,
)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_relationships_defaults_and_rollback(engine):
    with Session(engine) as session:
        user = User(login="resident", password_hash="hash", role=Role.RESIDENT)
        executor = Executor(name="Worker")
        request = ServiceRequest(
            resident=user, executor=executor, location="Hall", description="Leak"
        )
        entry = RequestHistory(
            request=request, actor=user, new_status=RequestStatus.DRAFT
        )
        session.add(entry)
        session.commit()
        assert request.status == RequestStatus.DRAFT
        assert entry in request.history and request in user.requests
        assert request.executor.name == "Worker"
        # SQLite не сохраняет timezone, поэтому проверяем UTC до повторной загрузки.
        another = User(login="another", password_hash="hash", role=Role.DISPATCHER)
        session.add(another)
        session.flush()
        assert another.created_at.tzinfo == UTC
        session.rollback()
        assert session.query(User).filter_by(login="another").count() == 0


def test_database_constraints_and_restricted_deletion(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, password_hash, role) "
                "VALUES (1, 'a', 'x', 'resident')"
            )
        )
        conn.execute(text("INSERT INTO executors (id, name) VALUES (1, 'worker')"))
        conn.execute(
            text(
                "INSERT INTO service_requests (id, resident_id, executor_id, "
                "location, description) "
                "VALUES (1, 1, 1, 'hall', 'leak')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO request_history (id, request_id, actor_id, new_status) "
                "VALUES (1, 1, 1, 'draft')"
            )
        )
        assert (
            conn.scalar(text("SELECT status FROM service_requests WHERE id=1"))
            == "draft"
        )
    statements = [
        "INSERT INTO users (login, password_hash, role) VALUES (' ', 'x', 'resident')",
        "INSERT INTO users (login, password_hash, role) VALUES ('a', 'x', 'resident')",
        "INSERT INTO users (login, password_hash, role) VALUES ('b', 'x', 'admin')",
        "INSERT INTO executors (name) VALUES ('  ')",
        "INSERT INTO service_requests (resident_id, location, description) "
        "VALUES (999, 'a', 'b')",
        "INSERT INTO service_requests (resident_id, location, description) "
        "VALUES (1, ' ', 'b')",
        "INSERT INTO service_requests (resident_id, location, description, status) "
        "VALUES (1, 'a', 'b', 'bad')",
        "INSERT INTO request_history (request_id, actor_id, new_status) "
        "VALUES (1, 1, 'bad')",
        "DELETE FROM users WHERE id=1",
        "DELETE FROM executors WHERE id=1",
        "DELETE FROM service_requests WHERE id=1",
    ]
    for statement in statements:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(statement))
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM request_history")) == 1


def test_update_timestamp_and_failed_commit_recovery(engine):
    old = datetime(2000, 1, 1, tzinfo=UTC)
    with Session(engine) as session:
        user = User(login="owner", password_hash="hash", role=Role.RESIDENT)
        request = ServiceRequest(
            resident=user,
            location="hall",
            description="leak",
            created_at=old,
            updated_at=old,
        )
        session.add(request)
        session.commit()
        created = request.created_at
        previous_update = request.updated_at
        request.description = "repaired"
        session.commit()
        assert request.created_at == created
        assert request.updated_at > previous_update

        session.add(User(login="owner", password_hash="hash", role=Role.RESIDENT))
        with pytest.raises(IntegrityError):
            session.commit()
        with pytest.raises(PendingRollbackError):
            session.query(User).count()
        session.rollback()
        session.add(User(login="other", password_hash="hash", role=Role.RESIDENT))
        session.commit()
        assert session.query(User).count() == 2

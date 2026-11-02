import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DatabaseSettings
from app.database import database_url, get_db


@pytest.fixture(autouse=True)
def clear_database_env(monkeypatch):
    for name in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_url_preserves_special_password_without_showing_it():
    password = "example:@/ #?%"
    settings = DatabaseSettings(_env_file=None, postgres_password=password)

    url = database_url(settings)

    assert url.password == password
    assert url.host == "127.0.0.1"
    assert url.port == 5432
    assert url.database == "housing_requests"
    assert password not in str(url)
    assert password not in repr(settings)


@pytest.mark.parametrize("password", ["", "   "])
def test_empty_password_fails_at_database_use(password):
    settings = DatabaseSettings(_env_file=None, postgres_password=password)
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        database_url(settings)


def test_missing_password_fails_at_engine_use(monkeypatch):
    from app import database

    monkeypatch.setattr(
        database, "DatabaseSettings", lambda: DatabaseSettings(_env_file=None)
    )
    database.get_engine.cache_clear()
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        database.get_engine()
    database.get_engine.cache_clear()


def test_dependency_rolls_back_on_error_and_closes(monkeypatch):
    from app import database

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (name TEXT)"))

    closed = []
    rolled_back = []

    class TrackingSession(Session):
        def rollback(self):
            rolled_back.append(True)
            super().rollback()

        def close(self):
            closed.append(True)
            super().close()

    factory = sessionmaker(bind=engine, class_=TrackingSession)
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)
    dependency = get_db()
    session = next(dependency)
    session.execute(text("INSERT INTO items VALUES ('example')"))
    with pytest.raises(RuntimeError, match="request failed"):
        dependency.throw(RuntimeError("request failed"))

    assert rolled_back == [True]
    assert closed == [True]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM items")) == 0
    engine.dispose()


def test_dependency_closes_without_automatic_commit(monkeypatch):
    from app import database

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (name TEXT)"))
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)
    dependency = get_db()
    session = next(dependency)
    session.execute(text("INSERT INTO items VALUES ('example')"))
    with pytest.raises(StopIteration):
        next(dependency)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM items")) == 0
    engine.dispose()


def test_dependency_allows_explicit_commit(monkeypatch):
    from app import database

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE items (name TEXT)"))
    monkeypatch.setattr(
        database, "get_session_factory", lambda: sessionmaker(bind=engine)
    )
    dependency = get_db()
    session = next(dependency)
    session.execute(text("INSERT INTO items VALUES ('example')"))
    session.commit()
    with pytest.raises(StopIteration):
        next(dependency)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM items")) == 1
    engine.dispose()


def test_engine_and_factory_are_lazy_and_reused(monkeypatch):
    from app import database

    database.get_session_factory.cache_clear()
    database.get_engine.cache_clear()
    engine = create_engine("sqlite://")
    calls = []
    connections = []
    event.listen(engine, "connect", lambda *args: connections.append(True))
    monkeypatch.setattr(
        database,
        "DatabaseSettings",
        lambda: DatabaseSettings(_env_file=None, postgres_password="example"),
    )
    monkeypatch.setattr(
        database, "create_engine", lambda url: calls.append(url) or engine
    )
    try:
        assert calls == []
        assert database.get_engine() is engine
        assert database.get_engine() is engine
        assert database.get_session_factory() is database.get_session_factory()
        assert len(calls) == 1
        assert connections == []
    finally:
        database.get_session_factory.cache_clear()
        database.get_engine.cache_clear()
        engine.dispose()


def test_import_does_not_create_engine_or_connect(tmp_path):
    code = """
import sqlalchemy

def fail(*args, **kwargs):
    raise AssertionError('engine created or connected during import')

sqlalchemy.create_engine = fail
sqlalchemy.engine.Engine.connect = fail
from app.config import Settings
Settings.model_config["env_file"] = None
import app.database
import app.main
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POSTGRES_", "APP_"))
    }
    env["POSTGRES_PORT"] = "invalid"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": os.getcwd()},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

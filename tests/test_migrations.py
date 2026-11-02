from io import StringIO

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.models import Base


def test_migration_upgrade_downgrade_and_metadata():
    engine = create_engine("sqlite://")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        assert (
            compare_metadata(MigrationContext.configure(connection), Base.metadata)
            == []
        )
        assert "service_requests" in inspect(connection).get_table_names()
        command.downgrade(config, "base")
        assert "service_requests" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        assert (
            compare_metadata(MigrationContext.configure(connection), Base.metadata)
            == []
        )
    engine.dispose()


def test_offline_sql_with_special_password(monkeypatch, capsys):
    monkeypatch.setenv("POSTGRES_PASSWORD", "fictional%40@/:password")
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE service_requests" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "CONSTRAINT request_status CHECK" in sql
    assert "fictional" not in sql
    captured = capsys.readouterr()
    assert "fictional" not in captured.out + captured.err


def test_online_constructs_url_without_interpolation(monkeypatch, tmp_path):
    password = "fictional%40@/:password"
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    sqlite_url = f"sqlite:///{tmp_path / 'migration.db'}"
    sqlite_engine = create_engine(sqlite_url)
    captured_urls = []

    def isolated_engine(url):
        captured_urls.append(url)
        assert url.drivername == "postgresql+psycopg"
        assert url.password == password
        return sqlite_engine

    monkeypatch.setattr("sqlalchemy.create_engine", isolated_engine)
    command.upgrade(Config("alembic.ini"), "head")
    assert len(captured_urls) == 1
    verification_engine = create_engine(sqlite_url)
    assert "service_requests" in inspect(verification_engine).get_table_names()
    verification_engine.dispose()

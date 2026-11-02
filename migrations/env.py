"""Запуск миграций без записи URL с паролем в конфигурацию Alembic."""

from alembic import context
from sqlalchemy import create_engine

from app.config import DatabaseSettings
from app.database import database_url
from app.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = database_url(DatabaseSettings())
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Тесты могут передать уже открытое изолированное соединение.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url(DatabaseSettings()))
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

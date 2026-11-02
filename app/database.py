"""Ленивое подключение к PostgreSQL и сессии для обработчиков FastAPI."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import DatabaseSettings


def database_url(settings: DatabaseSettings) -> URL:
    password = settings.postgres_password.get_secret_value()
    if not password.strip():
        raise ValueError("Укажите непустой POSTGRES_PASSWORD для подключения к БД")
    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.postgres_user,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Создать engine при первом обращении, без соединения при импорте."""
    return create_engine(database_url(DatabaseSettings()))


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine())


def get_db() -> Iterator[Session]:
    """Выдать сессию; при ошибке откатить изменения и всегда закрыть её."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

"""Явно заполнить демо-справочник, сохранив существующих исполнителей."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session_factory
from app.models import Executor

DEMO_EXECUTORS = (
    ("Демо-сантехник", "Сантехнические работы"),
    ("Демо-электрик", "Электромонтажные работы"),
)


def seed_executors(db: Session) -> list[str]:
    outcomes = []
    with db.begin():
        for name, specialization in DEMO_EXECUTORS:
            executor = db.scalar(select(Executor).where(Executor.name == name))
            if executor is None:
                db.add(Executor(name=name, specialization=specialization))
                outcomes.append(f"{name}: создан")
            else:
                outcomes.append(f"{name}: уже существует")
    return outcomes


def main() -> None:
    with get_session_factory()() as db:
        for outcome in seed_executors(db):
            print(outcome)


if __name__ == "__main__":
    main()

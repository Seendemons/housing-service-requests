"""Явно создать двух демо-пользователей без перезаписи существующих."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import DemoSettings
from app.database import get_session_factory
from app.models import Role, User


def seed_demo(db: Session, settings: DemoSettings) -> list[str]:
    accounts = [
        ("resident", Role.RESIDENT, settings.demo_resident_password.get_secret_value()),
        (
            "dispatcher",
            Role.DISPATCHER,
            settings.demo_dispatcher_password.get_secret_value(),
        ),
    ]
    if any(not password.strip() for _, _, password in accounts):
        raise ValueError("Укажите оба непустых демо-пароля")
    outcomes = []
    with db.begin():
        for login, role, password in accounts:
            user = db.scalar(select(User).where(User.login == login))
            if user is not None:
                if user.role != role:
                    raise ValueError(f"Конфликт роли пользователя {login}")
                outcomes.append(f"{login}: уже существует")
            else:
                db.add(
                    User(login=login, role=role, password_hash=hash_password(password))
                )
                outcomes.append(f"{login}: создан")
    return outcomes


def main() -> None:
    settings = DemoSettings()
    with get_session_factory()() as db:
        for outcome in seed_demo(db, settings):
            print(outcome)


if __name__ == "__main__":
    main()

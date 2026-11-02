import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password
from app.config import DemoSettings
from app.models import Base, Role, User
from app.seed_demo import seed_demo


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def demo(resident="resident-test", dispatcher="dispatcher-test"):
    return DemoSettings(
        _env_file=None,
        demo_resident_password=resident,
        demo_dispatcher_password=dispatcher,
    )


def test_seed_idempotent_preserves_existing(db):
    assert seed_demo(db, demo()) == ["resident: создан", "dispatcher: создан"]
    assert seed_demo(db, demo("changed", "changed")) == [
        "resident: уже существует",
        "dispatcher: уже существует",
    ]
    users = db.scalars(select(User).order_by(User.login)).all()
    assert len(users) == 2
    assert verify_password("resident-test", users[1].password_hash)
    assert users[1].role == Role.RESIDENT


def test_seed_conflict_rolls_back_both(db):
    with db.begin():
        db.add(
            User(
                login="dispatcher",
                role=Role.RESIDENT,
                password_hash=hash_password("existing"),
            )
        )
    with pytest.raises(ValueError, match="Конфликт"):
        seed_demo(db, demo())
    assert db.scalar(select(User).where(User.login == "resident")) is None
    assert (
        db.scalar(select(User).where(User.login == "dispatcher")).role == Role.RESIDENT
    )


def test_seed_rejects_empty_before_writes(db):
    with pytest.raises(ValueError):
        seed_demo(db, demo(dispatcher=" "))
    assert db.scalars(select(User)).all() == []


def test_main_prints_only_outcomes(db, monkeypatch, capsys):
    from app import seed_demo as module

    engine = db.get_bind()
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(module, "DemoSettings", lambda: demo())
    monkeypatch.setattr(
        module, "get_session_factory", lambda: sessionmaker(bind=engine)
    )
    module.main()
    output = capsys.readouterr()
    assert output.out == "resident: создан\ndispatcher: создан\n"
    assert output.err == ""
    assert "resident-test" not in output.out
    assert "$argon2" not in output.out

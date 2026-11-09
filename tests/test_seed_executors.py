import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Executor, User
from app.seed_executors import DEMO_EXECUTORS, seed_executors


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_seed_idempotent_preserves_existing_and_creates_no_accounts(db):
    with db.begin():
        db.add(Executor(name=DEMO_EXECUTORS[0][0], specialization="Своя специализация"))
        db.add(Executor(name="Существующий мастер", specialization="Отделка"))
    assert seed_executors(db) == [
        "Демо-сантехник: уже существует",
        "Демо-электрик: создан",
    ]
    assert seed_executors(db) == [
        "Демо-сантехник: уже существует",
        "Демо-электрик: уже существует",
    ]
    rows = db.scalars(select(Executor).order_by(Executor.id)).all()
    assert [(r.name, r.specialization) for r in rows] == [
        ("Демо-сантехник", "Своя специализация"),
        ("Существующий мастер", "Отделка"),
        DEMO_EXECUTORS[1],
    ]
    assert db.scalars(select(User)).all() == []


def test_seed_rolls_back_all_new_rows_on_failure(db):
    def fail(mapper, connection, target):
        if target.name == DEMO_EXECUTORS[1][0]:
            raise RuntimeError("seed failed")

    event.listen(Executor, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="seed failed"):
            seed_executors(db)
    finally:
        event.remove(Executor, "before_insert", fail)
    assert db.scalars(select(Executor)).all() == []


def test_main_prints_outcomes(db, monkeypatch, capsys):
    from app import seed_executors as module

    monkeypatch.setattr(
        module, "get_session_factory", lambda: sessionmaker(bind=db.get_bind())
    )
    module.main()
    assert capsys.readouterr().out == "Демо-сантехник: создан\nДемо-электрик: создан\n"
    assert len(db.scalars(select(Executor)).all()) == 2

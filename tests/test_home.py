from fastapi.testclient import TestClient

from app.config import Settings


def test_home_page(monkeypatch):
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_DEBUG", raising=False)
    monkeypatch.setattr("app.config.Settings", lambda: Settings(_env_file=None))
    from app.main import app

    def no_database():
        raise AssertionError("Главная страница не должна обращаться к БД")

    monkeypatch.setattr("app.database.get_session_factory", no_database)
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Заявки в жилищную службу</h1>" in response.text
    assert (
        "Сообщайте о проблемах в доме и следите за выполнением заявок." in response.text
    )

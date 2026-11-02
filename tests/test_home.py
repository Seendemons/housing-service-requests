from fastapi.testclient import TestClient

from app.main import app


def test_home_page():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Заявки в жилищную службу</h1>" in response.text
    assert (
        "Сообщайте о проблемах в доме и следите за выполнением заявок." in response.text
    )

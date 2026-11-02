import re

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app, templates


def test_login_stylesheet_works_outside_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/login",
            "scheme": "http",
            "server": ("testserver", 80),
            "headers": [],
            "router": app.router,
        }
    )
    page = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": "test-token", "error": None},
    )
    stylesheet = re.search(r'<link rel="stylesheet" href="([^"]+)"', page.body.decode())
    assert stylesheet is not None
    with TestClient(app) as client:
        response = client.get(stylesheet.group(1))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.content

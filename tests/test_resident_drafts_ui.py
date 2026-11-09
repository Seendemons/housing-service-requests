from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app, templates
from app.models import RequestStatus, Role, ServiceRequest, User


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    def find(self, tag, **attributes):
        return [
            attrs
            for name, attrs in self.elements
            if name == tag and attributes.items() <= attrs.items()
        ]


@pytest.fixture
def draft():
    return ServiceRequest(
        id=42,
        resident_id=7,
        location='Подъезд 1 <script>alert("место")</script>',
        description='Первая строка\n<script>alert("описание")</script>',
        status=RequestStatus.DRAFT,
        created_at=datetime(2026, 11, 3, 9, 15, tzinfo=UTC),
    )


def render(name, *, status_code=200, **context):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/resident",
            "scheme": "http",
            "server": ("testserver", 80),
            "headers": [],
            "router": app.router,
        }
    )
    return templates.TemplateResponse(
        request=request,
        name=name,
        status_code=status_code,
        context={
            "user": User(id=7, login="resident<test>", role=Role.RESIDENT),
            "csrf_token": "test-token",
            "drafts": [],
            "location": "",
            "description": "",
            "errors": {},
            **context,
        },
    )


@pytest.mark.parametrize(
    "name", ["resident.html", "request_form.html", "request_detail.html"]
)
def test_draft_pages_account_logout_and_local_styles(
    name, draft, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    html = render(name, service_request=draft).body.decode()
    page = Page(html)
    assert page.find("form", method="post", action="/logout")
    assert page.find("input", type="hidden", name="csrf_token", value="test-token")
    assert page.find("button", type="submit")
    assert page.find("a", href="/")
    assert "Логин: resident<test> · Роль: житель" in "".join(page.text)
    assert not page.find("test")
    assert not page.find("script")
    stylesheets = page.find("link", rel="stylesheet")
    assert len(stylesheets) == 1
    with TestClient(app) as client:
        response = client.get(stylesheets[0]["href"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.content


def test_empty_list_links_to_creation():
    page = Page(render("resident.html").body.decode())
    assert page.find("a", href="/resident/requests/new")
    assert "У вас пока нет черновиков." in "".join(page.text)
    assert not page.find("li")


def test_list_preserves_provided_order_and_escapes_locations(draft):
    newer = ServiceRequest(id=43, location="Лифт", status=RequestStatus.DRAFT)
    page = Page(render("resident.html", drafts=[newer, draft]).body.decode())
    links = [a["href"] for a in page.find("a")]
    assert links.index("/resident/requests/43") < links.index("/resident/requests/42")
    assert draft.location in "".join(page.text)
    assert not page.find("script")
    assert "У вас пока нет черновиков." not in "".join(page.text)


def test_creation_form_has_contract_fields_and_accessible_labels():
    page = Page(render("request_form.html").body.decode())
    assert page.find("form", method="post", action="/resident/requests")
    csrf = page.find("input", type="hidden", name="csrf_token")
    assert len(csrf) == 2  # Separate CSRF fields for creation and logout.
    location = page.find("input", id="location", name="location")[0]
    description = page.find("textarea", id="description", name="description")[0]
    for field, maximum in [(location, "255"), (description, "10000")]:
        assert "required" in field
        assert field["maxlength"] == maximum
        assert "aria-invalid" not in field
        assert page.find("label", **{"for": field["id"]})
        for reference in field["aria-describedby"].split():
            assert page.find("p", id=reference)
    assert page.find("a", href="/resident")
    assert "Сохранить черновик" in page.text
    assert "ещё не будет передан диспетчеру" in "".join(page.text)


def test_invalid_form_retains_values_and_connects_escaped_errors():
    location = '"><script>alert(1)</script>'
    description = "</textarea><script>alert(2)</script>\nВторая строка"
    errors = {
        "location": "Проверьте <место>",
        "description": "Добавьте <описание>",
    }
    response = render(
        "request_form.html",
        status_code=422,
        location=location,
        description=description,
        errors=errors,
    )
    page = Page(response.body.decode())
    assert response.status_code == 422
    assert page.find("input", name="location")[0]["value"] == location
    assert description in "".join(page.text)
    assert not page.find("script")
    assert len(page.find("textarea")) == 1
    for name, error in errors.items():
        field = page.find("input" if name == "location" else "textarea", name=name)[0]
        assert field["aria-invalid"] == "true"
        assert f"{name}-error" in field["aria-describedby"].split()
        assert page.find("p", id=f"{name}-error", role="alert")
        assert error in page.text
    assert not page.find("место")
    assert not page.find("описание")


def test_detail_renders_model_content_safely_with_creation_time(draft):
    page = Page(render("request_detail.html", service_request=draft).body.decode())
    text = "".join(page.text)
    assert "Черновик № 42" in text
    assert draft.location in text
    assert draft.description in text
    assert "03.11.2026 09:15 UTC" in text
    assert not page.find("script")
    assert page.find("dd", **{"class": "draft-description"})
    assert page.find("a", href="/resident")
    assert "Ещё не передан диспетчеру" in text
    assert page.find("a", href="/resident/requests/42/edit")


def test_edit_form_has_current_values_save_and_cancel(draft):
    page = Page(
        render(
            "request_form.html",
            edit_id=draft.id,
            location=draft.location,
            description=draft.description,
        ).body.decode()
    )
    assert page.find("form", method="post", action="/resident/requests/42/edit")
    assert page.find("a", href="/resident/requests/42")
    assert page.find("input", name="location")[0]["value"] == draft.location
    assert draft.description in "".join(page.text)
    assert "Сохранить изменения" in page.text
    assert "Редактировать черновик № 42" in "".join(page.text)
    assert not page.find("script")
    assert len(page.find("textarea")) == 1
    assert page.find("input", name="csrf_token", value="test-token")

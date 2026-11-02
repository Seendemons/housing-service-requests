from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    check_csrf,
    create_session,
    get_session,
    require_role,
    set_session_cookie,
    verify_password,
)
from app.config import Settings
from app.database import get_db
from app.models import RequestStatus, Role, ServiceRequest, User

settings = Settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)
app.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
DB = Annotated[Session, Depends(get_db)]


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="index.html", context={"title": app.title}
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: DB) -> Response:
    session = get_session(request, db)
    if session is not None and session.user is not None:
        return RedirectResponse(f"/{session.user.role.value}", status_code=303)
    token = None
    if session is None:
        session, token = create_session(db)
        db.commit()
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": session.csrf_token, "error": None},
    )
    if token is not None:
        set_session_cookie(response, token, settings.session_cookie_secure)
    return response


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    db: DB,
    csrf_token: Annotated[str, Form()] = "",
    login: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    session = get_session(request, db)
    check_csrf(session, csrf_token)
    user = (
        db.scalar(select(User).where(User.login == login))
        if len(login) <= 255
        else None
    )
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": session.csrf_token,
                "error": "Неверный логин или пароль",
            },
            status_code=401,
        )
    db.delete(session)
    _, token = create_session(db, user)
    db.commit()
    response = RedirectResponse(f"/{user.role.value}", status_code=303)
    set_session_cookie(response, token, settings.session_cookie_secure)
    return response


@app.post("/logout")
def logout(request: Request, db: DB, csrf_token: Annotated[str, Form()] = ""):
    session = get_session(request, db)
    check_csrf(session, csrf_token)
    if session.user is None:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    db.delete(session)
    db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


def account(request: Request, db: Session, role: Role):
    session = get_session(request, db)
    user = require_role(session, role)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={"user": user, "csrf_token": session.csrf_token},
    )


@app.get("/resident")
def resident(request: Request, db: DB):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    drafts = db.scalars(
        select(ServiceRequest)
        .where(
            ServiceRequest.resident_id == user.id,
            ServiceRequest.status == RequestStatus.DRAFT,
        )
        .order_by(ServiceRequest.created_at.desc(), ServiceRequest.id.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="resident.html",
        context={"user": user, "csrf_token": session.csrf_token, "drafts": drafts},
    )


@app.get("/resident/requests/new")
def new_request(request: Request, db: DB):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="request_form.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "location": "",
            "description": "",
            "errors": {},
        },
    )


@app.post("/resident/requests")
def create_request(
    request: Request,
    db: DB,
    csrf_token: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    check_csrf(session, csrf_token)
    clean_location = location.strip()
    clean_description = description.strip()
    errors = {}
    if not clean_location:
        errors["location"] = "Укажите местоположение."
    elif len(clean_location) > 255:
        errors["location"] = "Местоположение должно содержать не более 255 символов."
    if not clean_description:
        errors["description"] = "Укажите описание."
    elif len(clean_description) > 10000:
        errors["description"] = "Описание должно содержать не более 10000 символов."
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="request_form.html",
            context={
                "user": user,
                "csrf_token": session.csrf_token,
                "location": location,
                "description": description,
                "errors": errors,
            },
            status_code=422,
        )
    service_request = ServiceRequest(
        resident_id=user.id,
        status=RequestStatus.DRAFT,
        location=clean_location,
        description=clean_description,
    )
    db.add(service_request)
    db.commit()
    return RedirectResponse(f"/resident/requests/{service_request.id}", status_code=303)


@app.get("/resident/requests/{request_id}")
def request_detail(request: Request, request_id: int, db: DB):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service_request = db.scalar(
        select(ServiceRequest).where(
            ServiceRequest.id == request_id,
            ServiceRequest.resident_id == user.id,
            ServiceRequest.status == RequestStatus.DRAFT,
        )
    )
    if service_request is None:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    return templates.TemplateResponse(
        request=request,
        name="request_detail.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "service_request": service_request,
        },
    )


@app.get("/dispatcher")
def dispatcher(request: Request, db: DB):
    return account(request, db, Role.DISPATCHER)

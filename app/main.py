from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

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
from app.models import (
    Executor,
    RequestHistory,
    RequestStatus,
    Role,
    ServiceRequest,
    User,
    utc_now,
)

settings = Settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)
app.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["status_labels"] = {
    RequestStatus.DRAFT: "Черновик",
    RequestStatus.NEW: "Новая",
    RequestStatus.IN_PROGRESS: "В работе",
    RequestStatus.CLOSED: "Закрыта",
}
DB = Annotated[Session, Depends(get_db)]
DISPATCHER_STATUSES = (
    RequestStatus.NEW,
    RequestStatus.IN_PROGRESS,
    RequestStatus.CLOSED,
)


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
        if len(login) <= 255 and "\x00" not in login
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


@app.get("/resident")
def resident(request: Request, db: DB):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service_requests = db.scalars(
        select(ServiceRequest)
        .where(ServiceRequest.resident_id == user.id)
        .order_by(ServiceRequest.created_at.desc(), ServiceRequest.id.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="resident.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "service_requests": service_requests,
        },
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


def draft_form_errors(location: str, description: str) -> dict[str, str]:
    errors = {}
    if not location:
        errors["location"] = "Укажите местоположение."
    elif "\x00" in location:
        errors["location"] = "Местоположение не должно содержать нулевой символ."
    elif len(location) > 255:
        errors["location"] = "Местоположение должно содержать не более 255 символов."
    if not description:
        errors["description"] = "Укажите описание."
    elif "\x00" in description:
        errors["description"] = "Описание не должно содержать нулевой символ."
    elif len(description) > 10000:
        errors["description"] = "Описание должно содержать не более 10000 символов."
    return errors


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
    errors = draft_form_errors(clean_location, clean_description)
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


def get_own_draft(db: Session, request_id: int, resident_id: int) -> ServiceRequest:
    service_request = db.scalar(
        select(ServiceRequest).where(
            ServiceRequest.id == request_id,
            ServiceRequest.resident_id == resident_id,
            ServiceRequest.status == RequestStatus.DRAFT,
        )
    )
    if service_request is None:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    return service_request


def get_own_request(db: Session, request_id: int, resident_id: int) -> ServiceRequest:
    service_request = db.scalar(
        select(ServiceRequest).where(
            ServiceRequest.id == request_id,
            ServiceRequest.resident_id == resident_id,
        )
    )
    if service_request is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return service_request


def request_history(db: Session, request_id: int) -> list[RequestHistory]:
    return list(
        db.scalars(
            select(RequestHistory)
            .where(RequestHistory.request_id == request_id)
            .options(joinedload(RequestHistory.actor))
            .order_by(RequestHistory.created_at, RequestHistory.id)
        )
    )


@app.get("/resident/requests/{request_id}")
def request_detail(request: Request, request_id: int, db: DB):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service_request = get_own_request(db, request_id, user.id)
    return templates.TemplateResponse(
        request=request,
        name="request_detail.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "service_request": service_request,
            "history": request_history(db, service_request.id),
        },
    )


@app.get("/resident/requests/{request_id}/edit")
def edit_request_page(request: Request, request_id: int, db: DB):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service_request = get_own_draft(db, request_id, user.id)
    return templates.TemplateResponse(
        request=request,
        name="request_form.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "edit_id": service_request.id,
            "location": service_request.location,
            "description": service_request.description,
            "errors": {},
        },
    )


@app.post("/resident/requests/{request_id}/edit")
def edit_request(
    request: Request,
    request_id: int,
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
    service_request = get_own_draft(db, request_id, user.id)
    clean_location = location.strip()
    clean_description = description.strip()
    errors = draft_form_errors(clean_location, clean_description)
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="request_form.html",
            context={
                "user": user,
                "csrf_token": session.csrf_token,
                "edit_id": service_request.id,
                "location": location,
                "description": description,
                "errors": errors,
            },
            status_code=422,
        )
    # Повторно проверяем draft в UPDATE: отправка могла произойти после чтения формы.
    result = db.execute(
        update(ServiceRequest)
        .where(
            ServiceRequest.id == request_id,
            ServiceRequest.resident_id == user.id,
            ServiceRequest.status == RequestStatus.DRAFT,
        )
        .values(
            location=clean_location,
            description=clean_description,
            updated_at=utc_now(),
        )
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    db.commit()
    return RedirectResponse(f"/resident/requests/{service_request.id}", status_code=303)


@app.post("/resident/requests/{request_id}/submit")
def submit_request(
    request: Request,
    request_id: int,
    db: DB,
    csrf_token: Annotated[str, Form()] = "",
):
    session = get_session(request, db)
    user = require_role(session, Role.RESIDENT)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    check_csrf(session, csrf_token)
    submitted_at = utc_now()
    # Условный UPDATE допускает только одну отправку, в том числе параллельную.
    result = db.execute(
        update(ServiceRequest)
        .where(
            ServiceRequest.id == request_id,
            ServiceRequest.resident_id == user.id,
            ServiceRequest.status == RequestStatus.DRAFT,
        )
        .values(status=RequestStatus.NEW, updated_at=submitted_at)
    )
    if result.rowcount == 1:
        db.add(
            RequestHistory(
                request_id=request_id,
                actor_id=user.id,
                previous_status=RequestStatus.DRAFT,
                new_status=RequestStatus.NEW,
                created_at=submitted_at,
            )
        )
        db.commit()
    else:
        # Повторная отправка своей заявки возвращает в карточку без новой истории.
        get_own_request(db, request_id, user.id)
    return RedirectResponse(f"/resident/requests/{request_id}", status_code=303)


@app.get("/dispatcher")
def dispatcher(request: Request, db: DB, status: str = ""):
    session = get_session(request, db)
    user = require_role(session, Role.DISPATCHER)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if status and status not in DISPATCHER_STATUSES:
        raise HTTPException(status_code=422, detail="Недопустимый фильтр статуса")
    query = select(ServiceRequest).where(ServiceRequest.status.in_(DISPATCHER_STATUSES))
    if status:
        query = query.where(ServiceRequest.status == RequestStatus(status))
    service_requests = db.scalars(
        query.order_by(ServiceRequest.created_at.desc(), ServiceRequest.id.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="dispatcher.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "service_requests": service_requests,
            "selected_status": status,
            "status_options": DISPATCHER_STATUSES,
        },
    )


@app.get("/dispatcher/requests/{request_id}")
def dispatcher_request_detail(request: Request, request_id: int, db: DB):
    session = get_session(request, db)
    user = require_role(session, Role.DISPATCHER)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    service_request = get_dispatcher_request(db, request_id)
    return dispatcher_detail_response(request, db, session, user, service_request)


def get_dispatcher_request(db: Session, request_id: int) -> ServiceRequest:
    service_request = db.scalar(
        select(ServiceRequest)
        .where(
            ServiceRequest.id == request_id,
            ServiceRequest.status.in_(DISPATCHER_STATUSES),
        )
        .options(joinedload(ServiceRequest.resident))
    )
    if service_request is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return service_request


def dispatcher_detail_response(
    request,
    db,
    session,
    user,
    service_request,
    *,
    assignment_error=None,
    status_code=200,
):
    return templates.TemplateResponse(
        request=request,
        name="request_detail.html",
        context={
            "user": user,
            "csrf_token": session.csrf_token,
            "service_request": service_request,
            "history": request_history(db, service_request.id),
            "executors": db.scalars(
                select(Executor).order_by(Executor.name, Executor.id)
            ).all()
            if service_request.status == RequestStatus.NEW
            else [],
            "assignment_error": assignment_error,
        },
        status_code=status_code,
    )


@app.post("/dispatcher/requests/{request_id}/start")
def start_request(
    request: Request,
    request_id: int,
    db: DB,
    csrf_token: Annotated[str, Form()] = "",
    executor_id: Annotated[str, Form()] = "",
):
    session = get_session(request, db)
    user = require_role(session, Role.DISPATCHER)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    check_csrf(session, csrf_token)
    service_request = get_dispatcher_request(db, request_id)
    if service_request.status != RequestStatus.NEW:
        raise HTTPException(
            status_code=409, detail="В работу можно взять только новую заявку"
        )
    # Ограничиваем число до преобразования и SQL (integer в PostgreSQL).
    valid_id = (
        1 <= len(executor_id) <= 10
        and executor_id.isascii()
        and executor_id.isdecimal()
        and 1 <= int(executor_id) <= 2147483647
    )
    executor = db.get(Executor, int(executor_id)) if valid_id else None
    if executor is None:
        return dispatcher_detail_response(
            request,
            db,
            session,
            user,
            service_request,
            assignment_error="Выберите существующего исполнителя из справочника.",
            status_code=422,
        )
    started_at = utc_now()
    # Только один диспетчер может выполнить переход, даже из устаревшей формы.
    result = db.execute(
        update(ServiceRequest)
        .where(
            ServiceRequest.id == request_id, ServiceRequest.status == RequestStatus.NEW
        )
        .values(
            executor_id=executor.id,
            status=RequestStatus.IN_PROGRESS,
            updated_at=started_at,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=409, detail="В работу можно взять только новую заявку"
        )
    db.add(
        RequestHistory(
            request_id=request_id,
            actor_id=user.id,
            previous_status=RequestStatus.NEW,
            new_status=RequestStatus.IN_PROGRESS,
            comment=f"Назначен исполнитель: {executor.name}.",
            created_at=started_at,
        )
    )
    db.commit()
    return RedirectResponse(f"/dispatcher/requests/{request_id}", status_code=303)

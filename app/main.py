from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings

settings = Settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)

# Путь считается от этого файла, а не от папки запуска команды.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Показать главную страницу сайта."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": app.title},
    )

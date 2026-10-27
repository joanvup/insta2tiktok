from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import (
    APP_DIR,
    APP_TITLE,
    DEFAULT_REELS,
    MAX_ATTEMPTS,
    MAX_CAPTION,
    MAX_REELS,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_title": APP_TITLE,
            "max_caption": MAX_CAPTION,
            "max_reels": MAX_REELS,
            "default_reels": DEFAULT_REELS,
            "max_attempts": MAX_ATTEMPTS,
        },
    )

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .. import db
from ..config import MAX_CAPTION, SCREENSHOTS_DIR
from ..schemas import (
    InstagramSessionRequest,
    PublishRequest,
    ReelsRequest,
    RetryRequest,
)
from ..services.instagram import (
    InstagramAuthError,
    InstagramCooldownError,
    InstagramError,
    InstagramNetworkError,
    InstagramNotFoundError,
    InstagramPrivateError,
    InstagramRateLimitError,
    instagram_service,
)
from ..services.jobs import (
    DuplicateRiskError,
    JobError,
    JobNotFound,
    JobNotRetryable,
    MaxAttemptsError,
    job_manager,
)
from ..services.tiktok import tiktok_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ACTIVE_STATUSES = ("queued", "downloading", "uploading", "posting", "verifying")


def api_error(status_code: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.get("/status")
def get_status() -> dict[str, Any]:
    return {
        "instagram": instagram_service.status(),
        "tiktok": tiktok_service.status(),
        "worker": job_manager.status(),
    }


@router.post("/connect/instagram", status_code=202)
async def connect_instagram() -> dict[str, Any]:
    return instagram_service.start_connect(
        on_success=lambda: job_manager.resume_if_paused("instagram_auth")
    )


@router.get("/connect/instagram/status")
def connect_instagram_status() -> dict[str, Any]:
    return instagram_service.status()


@router.post("/instagram/session")
async def instagram_session(payload: InstagramSessionRequest) -> dict[str, Any]:
    try:
        username = await instagram_service.connect_with_sessionid(
            payload.sessionid,
            payload.csrftoken,
            on_success=lambda: job_manager.resume_if_paused("instagram_auth"),
        )
    except InstagramRateLimitError as exc:
        api_error(429, exc.code, exc.message)
    except InstagramAuthError as exc:
        api_error(401, exc.code, exc.message)
    except InstagramError as exc:
        api_error(400, exc.code, exc.message)
    return {"connected": True, "username": username}


@router.post("/connect/tiktok", status_code=202)
async def connect_tiktok() -> dict[str, Any]:
    return tiktok_service.start_connect(
        on_success=lambda: job_manager.resume_if_paused("tiktok_auth")
    )


@router.get("/connect/tiktok/status")
def connect_tiktok_status() -> dict[str, Any]:
    return tiktok_service.status()


@router.post("/reels")
async def get_reels(payload: ReelsRequest) -> dict[str, Any]:
    try:
        reels = await instagram_service.fetch_reels(payload.username, payload.limit)
    except InstagramAuthError as exc:
        api_error(401, exc.code, exc.message)
    except InstagramRateLimitError as exc:
        api_error(429, exc.code, exc.message)
    except InstagramCooldownError as exc:
        api_error(429, exc.code, exc.message)
    except InstagramPrivateError as exc:
        api_error(403, exc.code, exc.message)
    except InstagramNotFoundError as exc:
        api_error(404, exc.code, exc.message)
    except InstagramNetworkError as exc:
        api_error(502, exc.code, exc.message)
    except InstagramError as exc:
        api_error(502, exc.code, exc.message)
    return {
        "username": reels[0]["username"] if reels else payload.username,
        "reels": reels,
    }


@router.get("/reels/{shortcode}/thumbnail")
async def get_thumbnail(shortcode: str) -> FileResponse:
    if not SHORTCODE_RE.fullmatch(shortcode):
        api_error(404, "not_found", "Miniatura no encontrada.")
    try:
        path = await instagram_service.ensure_thumbnail(shortcode)
    except InstagramAuthError as exc:
        api_error(401, exc.code, exc.message)
    except InstagramRateLimitError as exc:
        api_error(429, exc.code, exc.message)
    except InstagramError as exc:
        api_error(502, exc.code, exc.message)
    if path is None or not Path(path).exists():
        api_error(404, "thumbnail_not_found", "Miniatura no disponible.")
    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/publish", status_code=201)
async def publish(payload: PublishRequest) -> dict[str, Any]:
    if not payload.confirm_rights:
        api_error(
            400,
            "rights_required",
            "Confirma que tienes derechos sobre el contenido antes de publicar.",
        )
    if not instagram_service.is_connected():
        api_error(
            409,
            "instagram_not_connected",
            "Conecta Instagram antes de publicar.",
        )
    if not tiktok_service.is_connected():
        api_error(
            409,
            "tiktok_not_connected",
            "Conecta TikTok antes de publicar.",
        )
    for item in payload.items:
        if len(item.caption) > MAX_CAPTION:
            api_error(
                422,
                "caption_too_long",
                f"La descripción de {item.shortcode} supera los {MAX_CAPTION} caracteres.",
            )
    created, skipped = job_manager.enqueue(
        [{"shortcode": item.shortcode, "caption": item.caption} for item in payload.items]
    )
    if not created:
        reason = skipped[0]["reason"] if skipped else "No hay reels para publicar."
        api_error(409, "nothing_to_publish", reason)
    return {"jobs": created, "skipped": skipped}


@router.get("/jobs")
def list_jobs(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    rows = db.fetch_all("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
    shortcodes = sorted({row["shortcode"] for row in rows})
    cache: dict[str, Any] = {}
    if shortcodes:
        placeholders = ",".join("?" for _ in shortcodes)
        for reel in db.fetch_all(
            f"SELECT shortcode, taken_at, duration, username FROM reels_cache WHERE shortcode IN ({placeholders})",
            shortcodes,
        ):
            cache[reel["shortcode"]] = reel
    jobs: list[dict[str, Any]] = []
    for row in rows:
        reel = cache.get(row["shortcode"])
        jobs.append(
            {
                "id": row["id"],
                "shortcode": row["shortcode"],
                "username": row["username"],
                "caption": row["caption"],
                "status": row["status"],
                "message": row["message"],
                "attempts": row["attempts"],
                "error": (
                    {"code": row["error_code"], "message": row["error_message"]}
                    if row["error_code"]
                    else None
                ),
                "screenshot_url": (
                    f"/api/jobs/{row['id']}/screenshot"
                    if row["screenshot_path"]
                    else None
                ),
                "post_clicked": bool(row["post_clicked"]),
                "active": row["status"] in ACTIVE_STATUSES,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "finished_at": row["finished_at"],
                "taken_at": reel["taken_at"] if reel else None,
                "duration": reel["duration"] if reel else None,
                "thumbnail_url": f"/api/reels/{row['shortcode']}/thumbnail",
            }
        )
    return {"jobs": jobs}


@router.get("/jobs/{job_id}/screenshot")
def get_screenshot(job_id: int) -> FileResponse:
    row = db.fetch_one("SELECT screenshot_path FROM jobs WHERE id = ?", (job_id,))
    if row is None or not row["screenshot_path"]:
        api_error(404, "screenshot_not_found", "No hay captura para ese trabajo.")
    path = Path(row["screenshot_path"]).resolve()
    root = SCREENSHOTS_DIR.resolve()
    if root not in path.parents or not path.exists():
        api_error(404, "screenshot_not_found", "No hay captura para ese trabajo.")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@router.post("/jobs/{job_id}/retry", status_code=201)
async def retry_job(job_id: int, payload: RetryRequest) -> dict[str, Any]:
    try:
        job = job_manager.retry(job_id, payload.acknowledge_duplicate_risk)
    except JobNotFound as exc:
        api_error(404, exc.code, exc.message)
    except DuplicateRiskError as exc:
        api_error(409, exc.code, exc.message)
    except MaxAttemptsError as exc:
        api_error(409, exc.code, exc.message)
    except JobNotRetryable as exc:
        api_error(409, exc.code, exc.message)
    except JobError as exc:
        api_error(400, exc.code, exc.message)
    return {"job": job}


@router.post("/worker/resume")
async def resume_worker() -> dict[str, Any]:
    job_manager.resume()
    return job_manager.status()

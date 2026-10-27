from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Any

from .. import db
from ..config import MAX_ATTEMPTS, POST_DELAY_SECONDS
from .instagram import (
    InstagramAuthError,
    InstagramError,
    InstagramRateLimitError,
    InstagramService,
    instagram_service,
)
from .tiktok import (
    TikTokBrowserError,
    TikTokError,
    TikTokLoginRequired,
    TikTokPostUncertain,
    TikTokPrivacyError,
    TikTokService,
    tiktok_service,
)

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "downloading", "uploading", "posting", "verifying")


class JobError(Exception):
    code = "job_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class JobNotFound(JobError):
    code = "job_not_found"


class JobNotRetryable(JobError):
    code = "job_not_retryable"


class DuplicateRiskError(JobError):
    code = "duplicate_risk"


class MaxAttemptsError(JobError):
    code = "max_attempts"


class JobManager:
    def __init__(self, instagram: InstagramService, tiktok: TikTokService) -> None:
        self.instagram = instagram
        self.tiktok = tiktok
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._paused: dict[str, str] | None = None
        self._active_job_id: int | None = None

    async def start(self) -> None:
        self._reconcile_interrupted()
        self._task = asyncio.create_task(self._run_loop(), name="publish-worker")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.tiktok.shutdown()

    def _reconcile_interrupted(self) -> None:
        rows = db.fetch_all(
            "SELECT id, post_clicked FROM jobs WHERE status IN (?, ?, ?, ?, ?)",
            ACTIVE_STATUSES,
        )
        now = db.utcnow()
        for row in rows:
            message = (
                "La aplicación se reinició después de pulsar Publicar. "
                "Verifica en TikTok si el video se publicó antes de reintentar."
                if row["post_clicked"]
                else "La aplicación se reinició durante el proceso. Puedes reintentar este reel."
            )
            db.execute(
                """
                UPDATE jobs
                SET status = 'interrupted', message = ?, error_code = 'interrupted',
                    error_message = ?, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (message, message, now, now, row["id"]),
            )

    # ------------------------------------------------------------------
    # Estado y control
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        active = db.fetch_one(
            "SELECT id FROM jobs WHERE status IN (?, ?, ?, ?, ?) ORDER BY id LIMIT 1",
            ACTIVE_STATUSES,
        )
        queued = db.fetch_one("SELECT COUNT(*) AS total FROM jobs WHERE status = 'queued'")
        paused = self._paused
        return {
            "paused": paused is not None,
            "pause_code": paused["code"] if paused else None,
            "pause_message": paused["message"] if paused else None,
            "active_job_id": active["id"] if active else None,
            "queued": int(queued["total"]) if queued else 0,
        }

    def pause(self, code: str, message: str) -> None:
        self._paused = {"code": code, "message": message}
        logger.warning("Worker pausado (%s): %s", code, message)

    def resume(self) -> None:
        if self._paused is not None:
            logger.info("Worker reanudado.")
        self._paused = None
        self._wake.set()

    def resume_if_paused(self, code: str) -> None:
        if self._paused and self._paused["code"] == code:
            self.resume()

    # ------------------------------------------------------------------
    # Cola
    # ------------------------------------------------------------------
    def enqueue(self, items: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        seen: set[str] = set()
        now = db.utcnow()
        with db.get_conn() as conn:
            for item in items:
                shortcode = item["shortcode"]
                caption = item["caption"]
                if shortcode in seen:
                    skipped.append({"shortcode": shortcode, "reason": "Está repetido en la selección."})
                    continue
                seen.add(shortcode)
                reel = conn.execute(
                    "SELECT username FROM reels_cache WHERE shortcode = ?", (shortcode,)
                ).fetchone()
                if reel is None:
                    skipped.append(
                        {"shortcode": shortcode, "reason": "No está en la lista de reels cargada."}
                    )
                    continue
                published = conn.execute(
                    "SELECT 1 FROM jobs WHERE shortcode = ? AND status = 'published'",
                    (shortcode,),
                ).fetchone()
                if published:
                    skipped.append({"shortcode": shortcode, "reason": "Ya se publicó antes."})
                    continue
                active = conn.execute(
                    "SELECT 1 FROM jobs WHERE shortcode = ? AND status IN (?, ?, ?, ?, ?)",
                    (shortcode, *ACTIVE_STATUSES),
                ).fetchone()
                if active:
                    skipped.append(
                        {"shortcode": shortcode, "reason": "Ya está en la cola o publicándose."}
                    )
                    continue
                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO jobs
                            (shortcode, username, caption, status, message, attempts,
                             created_at, updated_at)
                        VALUES (?, ?, ?, 'queued', ?, 1, ?, ?)
                        """,
                        (
                            shortcode,
                            reel["username"],
                            caption,
                            "En cola.",
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError:
                    skipped.append(
                        {"shortcode": shortcode, "reason": "Ya está en la cola o publicándose."}
                    )
                    continue
                created.append(
                    {
                        "id": int(cursor.lastrowid or 0),
                        "shortcode": shortcode,
                        "status": "queued",
                    }
                )
        if created:
            self._wake.set()
        return created, skipped

    def retry(self, job_id: int, acknowledge_duplicate_risk: bool) -> dict[str, Any]:
        job = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job is None:
            raise JobNotFound("No se encontró ese trabajo.")
        if job["status"] == "published":
            raise JobNotRetryable("Ese reel ya se publicó correctamente.")
        if job["status"] in ACTIVE_STATUSES:
            raise JobNotRetryable("Ese trabajo todavía está en curso.")
        if job["status"] not in ("failed", "interrupted"):
            raise JobNotRetryable("Ese trabajo no se puede reintentar.")
        if int(job["attempts"]) >= MAX_ATTEMPTS:
            raise MaxAttemptsError(
                f"Se alcanzó el máximo de {MAX_ATTEMPTS} intentos para este reel."
            )
        if int(job["post_clicked"]) and not acknowledge_duplicate_risk:
            raise DuplicateRiskError(
                "Ese intento llegó a pulsar Publicar. Verifica el video en TikTok y confirma "
                "que quieres intentarlo de nuevo para evitar duplicados."
            )
        now = db.utcnow()
        try:
            new_id = db.execute(
                """
                INSERT INTO jobs
                    (shortcode, username, caption, status, message, attempts,
                     created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    job["shortcode"],
                    job["username"],
                    job["caption"],
                    "Reintento en cola.",
                    int(job["attempts"]) + 1,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise JobNotRetryable(
                "No se puede reintentar: el reel ya está en la cola o ya se publicó."
            ) from exc
        self._wake.set()
        return {"id": new_id, "shortcode": job["shortcode"], "status": "queued"}

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            if self._paused is not None:
                self._wake.clear()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                continue

            self._wake.clear()
            job = db.fetch_one(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
            )
            if job is None:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                continue

            await self._process(job)
            if self._stop.is_set():
                break
            if self._paused is None and self._has_queued():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=POST_DELAY_SECONDS)
                    break
                except asyncio.TimeoutError:
                    pass

    def _has_queued(self) -> bool:
        row = db.fetch_one("SELECT 1 FROM jobs WHERE status = 'queued' LIMIT 1")
        return row is not None

    async def _process(self, job: Any) -> None:
        job_id = int(job["id"])
        claimed = db.execute_count(
            """
            UPDATE jobs
            SET status = 'downloading', message = ?, started_at = ?, updated_at = ?,
                error_code = NULL, error_message = NULL, screenshot_path = NULL
            WHERE id = ? AND status = 'queued'
            """,
            ("Descargando el video de Instagram…", db.utcnow(), db.utcnow(), job_id),
        )
        if claimed == 0:
            return
        self._active_job_id = job_id
        try:
            video = await self.instagram.ensure_video(job["shortcode"])

            def on_step(status: str, message: str) -> None:
                self._set_progress(job_id, status, message)

            def on_before_post() -> None:
                self._set_progress(
                    job_id,
                    "posting",
                    "Pulsando Publicar…",
                    post_clicked=True,
                )

            result = await self.tiktok.publish(
                video_path=Path(video),
                caption=job["caption"],
                job_id=job_id,
                on_step=on_step,
                on_before_post=on_before_post,
            )
            self._mark_published(job_id, result)
        except InstagramAuthError as exc:
            self._mark_failed(job_id, exc.code, exc.message)
            self.pause(
                "instagram_auth",
                "La sesión de Instagram caducó. Vuelve a conectar Instagram; la cola se reanudará automáticamente.",
            )
        except InstagramRateLimitError as exc:
            self._mark_failed(job_id, exc.code, exc.message)
            self.pause(
                "instagram_rate",
                "Instagram limitó las solicitudes. Espera entre 15 y 60 minutos y pulsa Reanudar cola.",
            )
        except InstagramError as exc:
            self._mark_failed(job_id, exc.code, exc.message)
        except TikTokLoginRequired as exc:
            self._mark_failed(job_id, exc.code, exc.message, exc.screenshot_path)
            self.pause(
                "tiktok_auth",
                "La sesión de TikTok caducó. Vuelve a conectar TikTok; la cola se reanudará automáticamente.",
            )
        except TikTokPrivacyError as exc:
            self._mark_failed(job_id, exc.code, exc.message, exc.screenshot_path)
            self.pause(
                "tiktok_privacy",
                "No se pudo confirmar la privacidad pública en TikTok. Revisa la captura y pulsa Reanudar cola.",
            )
        except TikTokPostUncertain as exc:
            self._mark_failed(job_id, exc.code, exc.message, exc.screenshot_path)
            self.pause(
                "tiktok_uncertain",
                "No se pudo confirmar una publicación. Revisa tu cuenta de TikTok antes de reintentar; "
                "la cola queda pausada.",
            )
        except (TikTokBrowserError, TikTokError) as exc:
            self._mark_failed(job_id, exc.code, exc.message, exc.screenshot_path)
            clicked_row = db.fetch_one(
                "SELECT post_clicked FROM jobs WHERE id = ?", (job_id,)
            )
            clicked = bool(clicked_row and clicked_row["post_clicked"])
            if clicked:
                self.pause(
                    "tiktok_uncertain",
                    "No se pudo confirmar una publicación. Revisa tu cuenta de TikTok antes de reintentar; "
                    "la cola queda pausada.",
                )
            elif isinstance(exc, TikTokBrowserError):
                self.pause(
                    "tiktok_browser",
                    "El navegador de TikTok falló. Revisa la captura y pulsa Reanudar cola.",
                )
        except Exception:
            logger.exception("Error inesperado procesando el job %s", job_id)
            self._mark_failed(
                job_id,
                "unexpected_error",
                "Ocurrió un error inesperado al procesar este reel.",
            )
        finally:
            self._active_job_id = None

    def _set_progress(
        self,
        job_id: int,
        status: str,
        message: str,
        post_clicked: bool = False,
    ) -> None:
        if post_clicked:
            db.execute(
                """
                UPDATE jobs SET status = ?, message = ?, post_clicked = 1, updated_at = ?
                WHERE id = ?
                """,
                (status, message, db.utcnow(), job_id),
            )
            return
        db.execute(
            "UPDATE jobs SET status = ?, message = ?, updated_at = ? WHERE id = ?",
            (status, message, db.utcnow(), job_id),
        )

    def _mark_published(self, job_id: int, message: str) -> None:
        now = db.utcnow()
        db.execute(
            """
            UPDATE jobs
            SET status = 'published', message = ?, error_code = NULL, error_message = NULL,
                updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (message, now, now, job_id),
        )

    def _mark_failed(
        self,
        job_id: int,
        code: str,
        message: str,
        screenshot_path: str | None = None,
    ) -> None:
        now = db.utcnow()
        db.execute(
            """
            UPDATE jobs
            SET status = 'failed', message = ?, error_code = ?, error_message = ?,
                screenshot_path = COALESCE(?, screenshot_path), updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (message, code, message, screenshot_path, now, now, job_id),
        )


job_manager = JobManager(instagram_service, tiktok_service)

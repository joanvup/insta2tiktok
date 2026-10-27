from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import timezone
from pathlib import Path
from typing import Any, NoReturn

import instaloader
from instaloader import Instaloader, Post, Profile
from instaloader.exceptions import (
    ConnectionException,
    InstaloaderException,
    LoginRequiredException,
    ProfileNotExistsException,
    TooManyRequestsException,
)
from playwright.async_api import (
    Error as PlaywrightError,
    async_playwright,
)

from .. import db
from ..config import (
    CONNECT_TIMEOUT_SECONDS,
    FETCH_COOLDOWN_SECONDS,
    INSTAGRAM_COOKIES_PATH,
    INSTAGRAM_LOGIN_URL,
    INSTAGRAM_PROFILE_DIR,
    MAX_REELS,
    MAX_VIDEO_BYTES,
    THUMBS_DIR,
    VIDEOS_DIR,
)

logger = logging.getLogger(__name__)

COOKIE_NAMES = {"sessionid", "csrftoken", "ds_user_id", "mid", "rur", "ig_did"}
MIN_VIDEO_BYTES = 10_000


class InstagramError(Exception):
    code = "instagram_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InstagramAuthError(InstagramError):
    code = "instagram_auth_expired"


class InstagramRateLimitError(InstagramError):
    code = "instagram_rate_limited"


class InstagramNotFoundError(InstagramError):
    code = "instagram_not_found"


class InstagramPrivateError(InstagramError):
    code = "instagram_private"


class InstagramCooldownError(InstagramError):
    code = "instagram_cooldown"

    def __init__(self, remaining: int) -> None:
        super().__init__(
            f"Espera {remaining} segundos antes de volver a cargar reels para no saturar Instagram."
        )
        self.remaining = remaining


class InstagramNetworkError(InstagramError):
    code = "instagram_network_error"


def _translate_exception(exc: Exception) -> NoReturn:
    text = str(exc).lower()
    if isinstance(exc, TooManyRequestsException) or "429" in text or "too many requests" in text:
        raise InstagramRateLimitError(
            "Instagram limitó las solicitudes. Espera entre 15 y 60 minutos antes de intentarlo de nuevo."
        ) from exc
    if (
        isinstance(exc, LoginRequiredException)
        or "login required" in text
        or "401" in text
        or "not logged in" in text
        or "checkpoint" in text
        or "challenge_required" in text
    ):
        raise InstagramAuthError(
            "La sesión de Instagram no es válida o ha caducado. Vuelve a conectar la cuenta."
        ) from exc
    if isinstance(exc, ProfileNotExistsException) or "not found" in text or "404" in text:
        raise InstagramNotFoundError(
            "No se encontró esa cuenta de Instagram. Comprueba el nombre de usuario."
        ) from exc
    if "private" in text:
        raise InstagramPrivateError(
            "La cuenta de Instagram es privada. Esta aplicación solo funciona con cuentas públicas."
        ) from exc
    if isinstance(exc, (ConnectionException, OSError)):
        raise InstagramNetworkError(
            "No se pudo conectar con Instagram. Comprueba tu conexión e inténtalo de nuevo."
        ) from exc
    raise InstagramNetworkError(
        "Instagram devolvió una respuesta inesperada. Actualiza instaloader si el problema continúa."
    ) from exc


class InstagramService:
    def __init__(self) -> None:
        self._io_lock = asyncio.Lock()
        self._browser_lock = asyncio.Lock()
        self._connect_task: asyncio.Task[None] | None = None
        self._last_fetch_monotonic = 0.0
        self._connect_state: dict[str, Any] = {
            "state": "idle",
            "message": "",
            "username": None,
        }

    # ------------------------------------------------------------------
    # Estado y sesión
    # ------------------------------------------------------------------
    def _load_cookies(self) -> dict[str, str]:
        if not INSTAGRAM_COOKIES_PATH.exists():
            return {}
        try:
            data = json.loads(INSTAGRAM_COOKIES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("No se pudo leer el archivo de cookies de Instagram.")
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if k in COOKIE_NAMES and v}

    def _save_cookies(self, cookies: dict[str, str], username: str) -> None:
        INSTAGRAM_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = INSTAGRAM_COOKIES_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, INSTAGRAM_COOKIES_PATH)
        try:
            os.chmod(INSTAGRAM_COOKIES_PATH, 0o600)
        except OSError:
            pass
        db.set_setting("ig_username", username)
        db.set_setting("ig_connected_at", db.utcnow())
        self._connect_state = {
            "state": "connected",
            "message": f"Sesión de Instagram conectada como @{username}.",
            "username": username,
        }

    def is_connected(self) -> bool:
        return bool(self._load_cookies().get("sessionid")) and bool(db.get_setting("ig_username"))

    def status(self) -> dict[str, Any]:
        cookies = self._load_cookies()
        saved_username = db.get_setting("ig_username")
        remaining = max(
            0,
            int(FETCH_COOLDOWN_SECONDS - (time.monotonic() - self._last_fetch_monotonic)),
        )
        return {
            "connected": bool(cookies.get("sessionid")) and bool(saved_username),
            "username": saved_username,
            "state": self._connect_state.get("state", "idle"),
            "message": self._connect_state.get("message", ""),
            "cooldown_remaining": remaining,
        }

    def _make_loader(self, cookies: dict[str, str]) -> Instaloader:
        loader = Instaloader(
            quiet=True,
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
        )
        for name, value in cookies.items():
            loader.context._session.cookies.set(name, value, domain=".instagram.com")
        return loader

    def _validate_cookies_sync(self, cookies: dict[str, str]) -> str | None:
        loader = self._make_loader(cookies)
        try:
            return loader.test_login()
        except (InstaloaderException, OSError) as exc:
            logger.info("Validación de sesión de Instagram fallida: %s", type(exc).__name__)
            if isinstance(exc, TooManyRequestsException) or "429" in str(exc):
                raise InstagramRateLimitError(
                    "Instagram limitó las solicitudes. Espera unos minutos y vuelve a intentarlo."
                ) from exc
            return None

    # ------------------------------------------------------------------
    # Conexión mediante navegador
    # ------------------------------------------------------------------
    def start_connect(self, on_success: Any = None) -> dict[str, Any]:
        if self._connect_task and not self._connect_task.done():
            return self.status()
        self._connect_state = {
            "state": "waiting_login",
            "message": "Inicia sesión en la ventana de Chromium que se ha abierto.",
            "username": None,
        }
        self._connect_task = asyncio.create_task(self._connect_flow(on_success))
        return self.status()

    async def _connect_flow(self, on_success: Any = None) -> None:
        async with self._browser_lock:
            try:
                async with async_playwright() as playwright:
                    context = await playwright.chromium.launch_persistent_context(
                        user_data_dir=str(INSTAGRAM_PROFILE_DIR),
                        headless=False,
                        locale="es-ES",
                        viewport={"width": 1280, "height": 900},
                    )
                    try:
                        page = context.pages[0] if context.pages else await context.new_page()
                        await page.goto(
                            INSTAGRAM_LOGIN_URL,
                            wait_until="domcontentloaded",
                            timeout=90_000,
                        )
                        cookies: dict[str, str] = {}
                        username: str | None = None
                        last_validation = 0.0
                        deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
                        while time.monotonic() < deadline:
                            if page.is_closed():
                                raise InstagramError(
                                    "La ventana de Instagram se cerró antes de completar el inicio de sesión."
                                )
                            browser_cookies = await context.cookies(
                                ["https://www.instagram.com"]
                            )
                            cookies = {
                                item["name"]: item["value"]
                                for item in browser_cookies
                                if item.get("name") in COOKIE_NAMES and item.get("value")
                            }
                            now = time.monotonic()
                            if cookies.get("sessionid") and now - last_validation >= 5:
                                last_validation = now
                                username = await asyncio.to_thread(
                                    self._validate_cookies_sync, cookies
                                )
                                if username:
                                    break
                            await asyncio.sleep(2)
                        if not username:
                            raise InstagramError(
                                "No se detectó una sesión válida de Instagram. Vuelve a intentarlo e inicia sesión completamente."
                            )
                        self._save_cookies(cookies, username)
                        if on_success:
                            result = on_success()
                            if asyncio.iscoroutine(result):
                                await result
                    finally:
                        await context.close()
            except PlaywrightError as exc:
                message = _playwright_message(exc)
                self._connect_state = {
                    "state": "error",
                    "message": message,
                    "username": db.get_setting("ig_username"),
                }
                logger.warning("Error de Playwright conectando Instagram: %s", message)
            except InstagramError as exc:
                self._connect_state = {
                    "state": "error",
                    "message": exc.message,
                    "username": db.get_setting("ig_username"),
                }
            except Exception:
                logger.exception("Error inesperado conectando Instagram")
                self._connect_state = {
                    "state": "error",
                    "message": "No se pudo conectar Instagram. Revisa la consola para más detalles.",
                    "username": db.get_setting("ig_username"),
                }
            finally:
                self._connect_task = None

    async def connect_with_sessionid(
        self, sessionid: str, csrftoken: str | None, on_success: Any = None
    ) -> str:
        cookies = {"sessionid": sessionid.strip()}
        if csrftoken:
            cookies["csrftoken"] = csrftoken.strip()
        async with self._browser_lock:
            username = await asyncio.to_thread(self._validate_cookies_sync, cookies)
            if not username:
                raise InstagramAuthError(
                    "La cookie sessionid no es válida. Copia de nuevo la cookie o usa el inicio de sesión en el navegador."
                )
            self._save_cookies(cookies, username)
            if on_success:
                result = on_success()
                if asyncio.iscoroutine(result):
                    await result
            return username

    # ------------------------------------------------------------------
    # Reels
    # ------------------------------------------------------------------
    async def fetch_reels(self, username: str, limit: int) -> list[dict[str, Any]]:
        cookies = self._load_cookies()
        if not cookies.get("sessionid"):
            raise InstagramAuthError(
                "Conecta primero una cuenta de Instagram desde el botón Conectar."
            )
        limit = max(1, min(limit, MAX_REELS))
        elapsed = time.monotonic() - self._last_fetch_monotonic
        if elapsed < FETCH_COOLDOWN_SECONDS:
            raise InstagramCooldownError(
                max(1, int(FETCH_COOLDOWN_SECONDS - elapsed))
            )
        self._last_fetch_monotonic = time.monotonic()
        async with self._io_lock:
            try:
                return await asyncio.to_thread(
                    self._fetch_reels_sync, cookies, username.lstrip("@"), limit
                )
            except InstagramError:
                raise
            except Exception as exc:
                logger.exception("Error obteniendo reels de Instagram")
                _translate_exception(exc)

    def _fetch_reels_sync(
        self, cookies: dict[str, str], username: str, limit: int
    ) -> list[dict[str, Any]]:
        loader = self._make_loader(cookies)
        try:
            profile = Profile.from_username(loader.context, username)
            if profile.is_private:
                raise InstagramPrivateError(
                    "La cuenta de Instagram es privada. Esta aplicación solo funciona con cuentas públicas."
                )
            reels: list[dict[str, Any]] = []
            scanned = 0
            max_scan = max(limit * 4, 40)
            for post in profile.get_reels():
                scanned += 1
                if scanned > max_scan:
                    break
                if not post.is_video:
                    continue
                taken_at = post.date_utc.replace(tzinfo=timezone.utc).isoformat()
                reels.append(
                    {
                        "shortcode": post.shortcode,
                        "username": profile.username,
                        "caption": post.caption or "",
                        "taken_at": taken_at,
                        "duration": post.video_duration,
                        "thumb_url": post.url,
                    }
                )
                if len(reels) >= limit:
                    break
        except InstagramError:
            raise
        except Exception as exc:
            _translate_exception(exc)

        now = db.utcnow()
        with db.get_conn() as conn:
            for reel in reels:
                conn.execute(
                    """
                    INSERT INTO reels_cache
                        (shortcode, username, caption, taken_at, duration, thumb_url, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(shortcode) DO UPDATE SET
                        username = excluded.username,
                        caption = excluded.caption,
                        taken_at = excluded.taken_at,
                        duration = excluded.duration,
                        thumb_url = excluded.thumb_url,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        reel["shortcode"],
                        reel["username"],
                        reel["caption"],
                        reel["taken_at"],
                        reel["duration"],
                        reel["thumb_url"],
                        now,
                    ),
                )
            published: set[str] = set()
            if reels:
                placeholders = ",".join("?" for _ in reels)
                rows = conn.execute(
                    f"SELECT shortcode FROM jobs WHERE status = 'published' AND shortcode IN ({placeholders})",
                    [reel["shortcode"] for reel in reels],
                ).fetchall()
                published = {row["shortcode"] for row in rows}

        return [
            {
                "shortcode": reel["shortcode"],
                "username": reel["username"],
                "caption": reel["caption"],
                "taken_at": reel["taken_at"],
                "duration": reel["duration"],
                "already_published": reel["shortcode"] in published,
                "thumbnail_url": f"/api/reels/{reel['shortcode']}/thumbnail",
            }
            for reel in reels
        ]

    # ------------------------------------------------------------------
    # Miniaturas y videos
    # ------------------------------------------------------------------
    async def ensure_thumbnail(self, shortcode: str) -> Path | None:
        row = db.fetch_one(
            "SELECT thumb_path, thumb_url FROM reels_cache WHERE shortcode = ?",
            (shortcode,),
        )
        if not row:
            return None
        if row["thumb_path"]:
            path = Path(row["thumb_path"])
            if path.exists():
                return path
        cookies = self._load_cookies()
        if not cookies.get("sessionid"):
            raise InstagramAuthError("La sesión de Instagram ha caducado.")
        async with self._io_lock:
            try:
                return await asyncio.to_thread(
                    self._download_thumbnail_sync, cookies, shortcode, row["thumb_url"]
                )
            except InstagramError:
                raise
            except Exception:
                logger.exception("No se pudo descargar la miniatura %s", shortcode)
                return None

    def _download_thumbnail_sync(
        self, cookies: dict[str, str], shortcode: str, thumb_url: str | None
    ) -> Path | None:
        loader = self._make_loader(cookies)
        url = thumb_url
        if not url:
            try:
                post = Post.from_shortcode(loader.context, shortcode)
                url = post.url
            except Exception as exc:
                _translate_exception(exc)
        if not url:
            return None

        headers = {
            "Referer": "https://www.instagram.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        try:
            response = loader.context._session.get(url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                return None
            extension = {
                "image/webp": ".webp",
                "image/png": ".png",
                "image/avif": ".avif",
            }.get(content_type, ".jpg")
            THUMBS_DIR.mkdir(parents=True, exist_ok=True)
            target = THUMBS_DIR / f"{shortcode}{extension}"
            temp = target.with_suffix(target.suffix + ".part")
            with temp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)
            if temp.stat().st_size < 100:
                temp.unlink(missing_ok=True)
                return None
            os.replace(temp, target)
            db.execute(
                "UPDATE reels_cache SET thumb_path = ? WHERE shortcode = ?",
                (str(target), shortcode),
            )
            return target
        except InstagramError:
            raise
        except Exception as exc:
            logger.info("Miniatura no disponible para %s: %s", shortcode, type(exc).__name__)
            return None

    async def ensure_video(self, shortcode: str) -> Path:
        row = db.fetch_one(
            "SELECT video_path FROM reels_cache WHERE shortcode = ?", (shortcode,)
        )
        if row and row["video_path"]:
            cached = Path(row["video_path"])
            if cached.exists() and cached.stat().st_size >= MIN_VIDEO_BYTES:
                return cached
        cookies = self._load_cookies()
        if not cookies.get("sessionid"):
            raise InstagramAuthError(
                "La sesión de Instagram ha caducado. Vuelve a conectar la cuenta."
            )
        async with self._io_lock:
            try:
                return await asyncio.to_thread(self._download_video_sync, cookies, shortcode)
            except InstagramError:
                raise
            except Exception as exc:
                logger.exception("Error descargando el video %s", shortcode)
                _translate_exception(exc)

    def _download_video_sync(self, cookies: dict[str, str], shortcode: str) -> Path:
        loader = self._make_loader(cookies)
        try:
            post = Post.from_shortcode(loader.context, shortcode)
        except Exception as exc:
            _translate_exception(exc)
        if not post.is_video or not post.video_url:
            raise InstagramError("La publicación seleccionada no contiene un video válido.")

        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        target = VIDEOS_DIR / f"{shortcode}.mp4"
        temp = target.with_suffix(".mp4.part")
        headers = {
            "Referer": "https://www.instagram.com/",
            "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
        }
        try:
            response = loader.context._session.get(
                post.video_url, headers=headers, stream=True, timeout=120
            )
            response.raise_for_status()
            total = 0
            with temp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise InstagramError("El video supera el tamaño máximo admitido.")
                    handle.write(chunk)
            if total < MIN_VIDEO_BYTES:
                raise InstagramError("El video descargado está incompleto o vacío.")
            with temp.open("rb") as handle:
                header = handle.read(64)
            if b"ftyp" not in header:
                raise InstagramError("Instagram devolvió un archivo que no es un MP4 válido.")
            os.replace(temp, target)
        except InstagramError:
            temp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            temp.unlink(missing_ok=True)
            logger.info("Fallo descargando video %s: %s", shortcode, type(exc).__name__)
            _translate_exception(exc)

        db.execute(
            "UPDATE reels_cache SET video_path = ? WHERE shortcode = ?",
            (str(target), shortcode),
        )
        return target


def _playwright_message(exc: PlaywrightError) -> str:
    text = str(exc)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return "Chromium no está instalado. Ejecuta setup.bat para instalarlo."
    if "ProcessSingleton" in text or "user data directory is already in use" in text:
        return "El perfil de Chromium está en uso. Cierra la ventana de Instagram que quedó abierta y reintenta."
    if "has been closed" in text or "Target closed" in text:
        return "La ventana de Chromium se cerró antes de completar la conexión."
    return "No se pudo abrir el navegador para conectar Instagram. Revisa la consola."


instagram_service = InstagramService()

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .. import db
from ..config import (
    CONNECT_TIMEOUT_SECONDS,
    POST_CONFIRM_TIMEOUT_MS,
    POST_READY_TIMEOUT_MS,
    SCREENSHOTS_DIR,
    TIKTOK_PROFILE_DIR,
    TIKTOK_UPLOAD_URL,
    UPLOAD_READY_TIMEOUT_MS,
)

logger = logging.getLogger(__name__)

FILE_INPUT_SELECTORS = [
    'input[type="file"][accept="video/*"]',
    'input[type="file"]',
]
CAPTION_SELECTORS = [
    'div[data-contents="true"]',
    '[contenteditable="true"][role="textbox"]',
    ".public-DraftEditor-content",
    '[contenteditable="true"]',
]
POST_BUTTON_SELECTORS = [
    'button[data-e2e="post_video_button"]',
]
PRIVACY_LABEL_RE = re.compile(
    r"(who can watch this video|who can view this video|qui[eé]n puede ver este video|qui[eé]n puede verlo)",
    re.I,
)
PUBLIC_VALUE_RE = re.compile(r"(^|\b)(everyone|todos|p[uú]blico|public)(\b|$)", re.I)
NON_PUBLIC_VALUE_RE = re.compile(
    r"(^|\b)(friends|amigos|only me|solo yo|solo para m[ií]|followers|seguidores)(\b|$)",
    re.I,
)
SUCCESS_RE = re.compile(
    r"(content under review|under review|video is being uploaded|video posted|"
    r"your video is being processed|en revisi[oó]n|se est[aá] subiendo|publicado correctamente)",
    re.I,
)
POST_FAILURE_RE = re.compile(
    r"(something went wrong|algo sali[oó] mal|couldn['’]t post|no se pudo publicar|"
    r"upload failed|no se pudo subir|try again|int[eé]ntalo de nuevo)",
    re.I,
)
COPYRIGHT_RE = re.compile(
    r"(copyright check|copyrighted content|copyright infringement|"
    r"derechos de autor|infracci[oó]n de derechos)",
    re.I,
)
LOGIN_RE = re.compile(
    r"(log in to tiktok|inicia sesi[oó]n en tiktok|iniciar sesi[oó]n en tiktok)",
    re.I,
)
ACCEPT_BUTTON_RE = re.compile(
    r"^(accept all|accept all cookies|aceptar todo|aceptar todas|allow all|permitir todo|got it|entendido)$",
    re.I,
)


class TikTokError(Exception):
    code = "tiktok_error"

    def __init__(self, message: str, screenshot_path: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.screenshot_path = screenshot_path


class TikTokLoginRequired(TikTokError):
    code = "tiktok_login_required"


class TikTokPrivacyError(TikTokError):
    code = "tiktok_privacy_unconfirmed"


class TikTokUploadError(TikTokError):
    code = "tiktok_upload_failed"


class TikTokPostError(TikTokError):
    code = "tiktok_post_failed"


class TikTokPostUncertain(TikTokError):
    code = "tiktok_post_uncertain"


class TikTokBrowserError(TikTokError):
    code = "tiktok_browser_error"


ProgressCallback = Callable[[str, str], None]
BeforePostCallback = Callable[[], None]


class TikTokService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connect_task: asyncio.Task[None] | None = None
        self._active_context: BrowserContext | None = None
        self._connect_state: dict[str, Any] = {
            "state": "idle",
            "message": "",
        }

    # ------------------------------------------------------------------
    # Estado y conexión
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        return bool(db.get_setting("tiktok_connected_at"))

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.is_connected(),
            "state": self._connect_state.get("state", "idle"),
            "message": self._connect_state.get("message", ""),
        }

    def start_connect(self, on_success: Any = None) -> dict[str, Any]:
        if self._connect_task and not self._connect_task.done():
            return self.status()
        waiting_for_lock = self._lock.locked()
        self._connect_state = {
            "state": "waiting_login",
            "message": (
                "Esperando a que termine la publicación actual antes de abrir TikTok."
                if waiting_for_lock
                else "Inicia sesión en la ventana de Chromium que se ha abierto."
            ),
        }
        self._connect_task = asyncio.create_task(self._connect_flow(on_success))
        return self.status()

    async def _connect_flow(self, on_success: Any = None) -> None:
        async with self._lock:
            context: BrowserContext | None = None
            try:
                async with async_playwright() as playwright:
                    context = await self._launch(playwright)
                    self._active_context = context
                    page = context.pages[0] if context.pages else await context.new_page()
                    page.set_default_timeout(20_000)
                    await page.goto(
                        TIKTOK_UPLOAD_URL, wait_until="domcontentloaded", timeout=90_000
                    )
                    await self._dismiss_known_dialogs(page)
                    deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
                    connected = False
                    while time.monotonic() < deadline:
                        if page.is_closed():
                            raise TikTokBrowserError(
                                "La ventana de TikTok se cerró antes de completar el inicio de sesión."
                            )
                        if await self._upload_ready(page):
                            connected = True
                            break
                        if await self._login_visible(page):
                            self._connect_state = {
                                "state": "waiting_login",
                                "message": (
                                    "Inicia sesión en la ventana de Chromium. Puedes usar QR, "
                                    "contraseña o verificación en dos pasos; la app no guarda tus credenciales."
                                ),
                            }
                        else:
                            self._connect_state = {
                                "state": "waiting_login",
                                "message": "Esperando a que TikTok Studio termine de cargar…",
                            }
                        await asyncio.sleep(2)
                    if not connected:
                        raise TikTokLoginRequired(
                            "No se detectó una sesión válida de TikTok. Vuelve a intentarlo."
                        )
                    db.set_setting("tiktok_connected_at", db.utcnow())
                    self._connect_state = {
                        "state": "connected",
                        "message": (
                            "Sesión de TikTok guardada. Se comprobará de nuevo al publicar."
                        ),
                    }
                    if on_success:
                        result = on_success()
                        if asyncio.iscoroutine(result):
                            await result
            except TikTokError as exc:
                self._connect_state = {"state": "error", "message": exc.message}
            except PlaywrightError as exc:
                message = _playwright_message(exc)
                self._connect_state = {"state": "error", "message": message}
                logger.warning("Error de Playwright conectando TikTok: %s", message)
            except Exception:
                logger.exception("Error inesperado conectando TikTok")
                self._connect_state = {
                    "state": "error",
                    "message": "No se pudo conectar TikTok. Revisa la consola para más detalles.",
                }
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                self._active_context = None
                self._connect_task = None

    # ------------------------------------------------------------------
    # Publicación
    # ------------------------------------------------------------------
    async def publish(
        self,
        video_path: Path,
        caption: str,
        job_id: int,
        on_step: ProgressCallback,
        on_before_post: BeforePostCallback,
    ) -> str:
        if not video_path.exists():
            raise TikTokUploadError("No se encontró el archivo de video descargado.")

        async with self._lock:
            context: BrowserContext | None = None
            page: Page | None = None
            try:
                async with async_playwright() as playwright:
                    context = await self._launch(playwright)
                    self._active_context = context
                    page = context.pages[0] if context.pages else await context.new_page()
                    page.set_default_timeout(20_000)
                    await page.goto(
                        TIKTOK_UPLOAD_URL, wait_until="domcontentloaded", timeout=90_000
                    )
                    await self._dismiss_known_dialogs(page)

                    if not await self._wait_for_upload_page(page, timeout_ms=60_000):
                        raise TikTokLoginRequired(
                            "TikTok pidió iniciar sesión. Conecta la cuenta de nuevo antes de publicar."
                        )

                    on_step("uploading", "Subiendo el video a TikTok…")
                    await self._upload_video_file(page, video_path)
                    await self._fill_caption(page, caption)
                    await self._set_privacy_public(page)

                    post_button = await self._find_post_button(page)
                    await self._wait_until_post_ready(page, post_button)

                    on_step("posting", "Pulsando Publicar…")
                    on_before_post()
                    await post_button.click()
                    on_step("verifying", "Esperando la confirmación de TikTok…")

                    return await self._wait_for_result(page, job_id)
            except TikTokError as exc:
                if exc.screenshot_path is None and page is not None:
                    exc.screenshot_path = await self._safe_screenshot(page, job_id)
                raise
            except PlaywrightTimeoutError as exc:
                screenshot = (
                    await self._safe_screenshot(page, job_id) if page is not None else None
                )
                raise TikTokUploadError(
                    "TikTok tardó demasiado en responder. Revisa la publicación antes de reintentar.",
                    screenshot_path=screenshot,
                ) from exc
            except PlaywrightError as exc:
                screenshot = (
                    await self._safe_screenshot(page, job_id) if page is not None else None
                )
                message = _playwright_message(exc)
                raise TikTokBrowserError(message, screenshot_path=screenshot) from exc
            except Exception as exc:
                logger.exception("Error inesperado publicando en TikTok")
                screenshot = (
                    await self._safe_screenshot(page, job_id) if page is not None else None
                )
                raise TikTokPostError(
                    "Error inesperado durante la publicación en TikTok.",
                    screenshot_path=screenshot,
                ) from exc
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                self._active_context = None

    async def _launch(self, playwright: Any) -> BrowserContext:
        return await playwright.chromium.launch_persistent_context(
            user_data_dir=str(TIKTOK_PROFILE_DIR),
            headless=False,
            locale="es-ES",
            viewport={"width": 1440, "height": 1000},
        )

    async def _upload_video_file(self, page: Page, video_path: Path) -> None:
        input_locator: Locator | None = None
        for selector in FILE_INPUT_SELECTORS:
            locator = page.locator(selector).first
            try:
                if await locator.count():
                    input_locator = locator
                    break
            except PlaywrightError:
                continue
        if input_locator is not None:
            await input_locator.set_input_files(str(video_path))
            return

        select_button = page.get_by_role(
            "button",
            name=re.compile(r"(select video|seleccionar video|upload video|subir video)", re.I),
        ).first
        try:
            async with page.expect_file_chooser(timeout=15_000) as chooser_info:
                await select_button.click()
            chooser = await chooser_info.value
            await chooser.set_files(str(video_path))
        except PlaywrightError as exc:
            raise TikTokUploadError(
                "No se encontró el selector de archivos de TikTok Studio."
            ) from exc

    async def _fill_caption(self, page: Page, caption: str) -> None:
        editor = await self._find_visible(page, CAPTION_SELECTORS, timeout_ms=UPLOAD_READY_TIMEOUT_MS)
        if editor is None:
            raise TikTokUploadError(
                "No se encontró el campo de descripción de TikTok. La interfaz puede haber cambiado."
            )
        try:
            existing = (await editor.inner_text()).strip()
        except PlaywrightError:
            existing = ""
        await editor.click()
        if existing:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        expected = self._normalize_caption(caption)
        if caption:
            await page.keyboard.insert_text(caption)
            await page.wait_for_timeout(400)
            text = await self._read_caption(editor)
            if self._normalize_caption(text) != expected:
                try:
                    await editor.fill(caption)
                    await page.wait_for_timeout(300)
                except PlaywrightError:
                    pass
                text = await self._read_caption(editor)
            if self._normalize_caption(text) != expected:
                raise TikTokUploadError(
                    "La descripción no quedó guardada exactamente en TikTok. "
                    "La interfaz puede haber cambiado y no se pulsó Publicar."
                )
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        final_text = self._normalize_caption(await self._read_caption(editor))
        if final_text != expected:
            raise TikTokUploadError(
                "La descripción cambió después de escribirla. No se pulsó Publicar."
            )

    @staticmethod
    def _normalize_caption(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    async def _read_caption(self, editor: Locator) -> str:
        try:
            return await editor.inner_text()
        except PlaywrightError:
            return ""

    async def _find_visible(
        self, page: Page, selectors: list[str], timeout_ms: int
    ) -> Locator | None:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for frame in page.frames:
                for selector in selectors:
                    locator = frame.locator(selector).first
                    try:
                        if await locator.count() and await locator.is_visible():
                            return locator
                    except PlaywrightError:
                        continue
            await asyncio.sleep(0.4)
        return None

    async def _find_visible_text(
        self, page: Page, pattern: re.Pattern[str], timeout_ms: int
    ) -> Locator | None:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for frame in page.frames:
                locator = frame.get_by_text(pattern).first
                try:
                    if await locator.count() and await locator.is_visible():
                        return locator
                except PlaywrightError:
                    continue
            await asyncio.sleep(0.4)
        return None

    async def _set_privacy_public(self, page: Page) -> None:
        label = await self._find_visible_text(page, PRIVACY_LABEL_RE, timeout_ms=8_000)
        if label is None:
            await self._try_expand_options(page)
            label = await self._find_visible_text(page, PRIVACY_LABEL_RE, timeout_ms=5_000)
        if label is None:
            raise TikTokPrivacyError(
                "No se encontró el ajuste 'Quién puede ver este video'. Por seguridad no se publicó nada. "
                "Abre TikTok Studio manualmente para revisar la interfaz."
            )

        try:
            await label.scroll_into_view_if_needed(timeout=3_000)
        except PlaywrightError:
            pass

        if await self._privacy_value_is_public(label):
            return

        control = await self._find_privacy_control(page, label)
        if control is None:
            raise TikTokPrivacyError(
                "No se pudo abrir el selector de privacidad de TikTok. No se publicó nada."
            )

        try:
            tag_name = await control.evaluate("element => element.tagName.toLowerCase()")
        except PlaywrightError:
            tag_name = ""

        if tag_name == "select":
            options = await control.locator("option").all_text_contents()
            public_option = next(
                (
                    option.strip()
                    for option in options
                    if PUBLIC_VALUE_RE.search(option)
                    and not NON_PUBLIC_VALUE_RE.search(option)
                ),
                None,
            )
            if not public_option:
                raise TikTokPrivacyError(
                    "El selector de privacidad no ofrece una opción pública."
                )
            await control.select_option(label=public_option)
            selected = await control.evaluate(
                "element => element.options[element.selectedIndex]?.textContent || ''"
            )
            if not PUBLIC_VALUE_RE.search(selected) or NON_PUBLIC_VALUE_RE.search(selected):
                raise TikTokPrivacyError(
                    "No se pudo confirmar la opción pública en el selector de privacidad."
                )
        else:
            await control.click()
            await page.wait_for_timeout(600)
            option = await self._find_public_option(page)
            if option is None:
                raise TikTokPrivacyError(
                    "No se encontró la opción 'Todos' en el selector de privacidad de TikTok."
                )
            await option.click()
            await page.wait_for_timeout(600)

        fresh_label = await self._find_visible_text(page, PRIVACY_LABEL_RE, timeout_ms=3_000)
        if fresh_label is None or not await self._privacy_value_is_public(fresh_label):
            raise TikTokPrivacyError(
                "No se pudo confirmar que el video será visible para todos. No se publicó nada."
            )

    async def _privacy_value_is_public(self, label: Locator) -> bool:
        for depth in (1, 2, 3):
            try:
                text = await label.locator(f"xpath=ancestor::*[{depth}]").inner_text(
                    timeout=1_500
                )
            except PlaywrightError:
                continue
            if NON_PUBLIC_VALUE_RE.search(text):
                return False
            if PUBLIC_VALUE_RE.search(text):
                return True
        return False

    async def _find_privacy_control(self, page: Page, label: Locator) -> Locator | None:
        selects = page.locator("select")
        try:
            select_count = await selects.count()
        except PlaywrightError:
            select_count = 0
        for index in range(select_count):
            select = selects.nth(index)
            try:
                options = await select.locator("option").all_text_contents()
            except PlaywrightError:
                continue
            if any(PUBLIC_VALUE_RE.search(option) for option in options):
                return select

        relative_xpaths = [
            "xpath=ancestor::*[1]//*[@role='combobox']",
            "xpath=ancestor::*[2]//*[@role='combobox']",
            "xpath=ancestor::*[3]//*[@role='combobox']",
            "xpath=ancestor::*[1]//button",
            "xpath=ancestor::*[2]//button",
            "xpath=ancestor::*[3]//button",
            "xpath=following-sibling::*[1]//*[@role='combobox']",
            "xpath=following-sibling::*[1]//button",
        ]
        for xpath in relative_xpaths:
            try:
                candidate = label.locator(xpath).first
                if await candidate.count() and await candidate.is_visible():
                    return candidate
            except PlaywrightError:
                continue
        return None

    async def _find_public_option(self, page: Page) -> Locator | None:
        pattern = re.compile(r"^(everyone|todos|p[uú]blico|public)$", re.I)
        candidates: list[Locator] = []
        for frame in page.frames:
            locator = frame.get_by_text(pattern)
            try:
                count = await locator.count()
            except PlaywrightError:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    if await item.is_visible():
                        candidates.append(item)
                except PlaywrightError:
                    continue
        return candidates[-1] if candidates else None

    async def _try_expand_options(self, page: Page) -> None:
        pattern = re.compile(r"^(show more|show more options|mostrar m[aá]s|m[aá]s opciones)$", re.I)
        for frame in page.frames:
            button = frame.get_by_role("button", name=pattern).first
            try:
                if await button.count() and await button.is_visible():
                    await button.click()
                    await page.wait_for_timeout(500)
                    return
            except PlaywrightError:
                continue

    async def _find_post_button(self, page: Page) -> Locator:
        for selector in POST_BUTTON_SELECTORS:
            locator = page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return locator
            except PlaywrightError:
                continue
        buttons = page.get_by_role(
            "button", name=re.compile(r"^(post|publicar|publish)$", re.I)
        )
        try:
            count = await buttons.count()
        except PlaywrightError:
            count = 0
        for index in range(count - 1, -1, -1):
            locator = buttons.nth(index)
            try:
                if await locator.is_visible():
                    return locator
            except PlaywrightError:
                continue
        raise TikTokUploadError(
            "No se encontró el botón Publicar de TikTok. La interfaz puede haber cambiado."
        )

    async def _wait_until_post_ready(self, page: Page, button: Locator) -> None:
        deadline = time.monotonic() + POST_READY_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            if page.is_closed():
                raise TikTokBrowserError("La ventana de Chromium se cerró durante la subida.")
            if await self._visible_text(page, POST_FAILURE_RE):
                raise TikTokUploadError("TikTok informó de un error al subir el video.")
            if await self._login_visible(page):
                raise TikTokLoginRequired(
                    "La sesión de TikTok caducó durante la subida. Conecta la cuenta de nuevo."
                )
            try:
                if await button.is_visible() and await button.is_enabled():
                    return
            except PlaywrightError:
                pass
            await asyncio.sleep(2)
        raise TikTokUploadError(
            "TikTok no terminó de procesar el video a tiempo. No se pulsó Publicar."
        )

    async def _wait_for_result(self, page: Page, job_id: int) -> str:
        deadline = time.monotonic() + POST_CONFIRM_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            if page.is_closed():
                raise TikTokPostUncertain(
                    "La ventana se cerró después de pulsar Publicar. Verifica en TikTok si el video se publicó."
                )
            if await self._login_visible(page):
                raise TikTokPostUncertain(
                    "La sesión de TikTok caducó después de pulsar Publicar. Verifica en TikTok antes de reintentar."
                )
            if await self._visible_text(page, COPYRIGHT_RE):
                raise TikTokPostUncertain(
                    "TikTok pidió una confirmación de derechos de autor. Revisa la cuenta y termina la publicación manualmente."
                )
            if await self._visible_text(page, POST_FAILURE_RE):
                raise TikTokPostUncertain(
                    "TikTok mostró un error después de pulsar Publicar. Verifica en la cuenta antes de reintentar."
                )
            if await self._visible_text(page, SUCCESS_RE):
                return "TikTok aceptó el video. Puede aparecer como 'en revisión' durante unos minutos."
            parsed = urlsplit(page.url)
            if (
                parsed.netloc.endswith("tiktok.com")
                and "/tiktokstudio" in parsed.path
                and "/upload" not in parsed.path
                and "/login" not in parsed.path
            ):
                return "TikTok aceptó el video y redirigió a la gestión de contenidos."
            await page.wait_for_timeout(1_500)
        raise TikTokPostUncertain(
            "No se pudo confirmar la publicación después de pulsar Publicar. "
            "Abre TikTok y verifica si el video aparece antes de reintentar."
        )

    async def _visible_text(self, page: Page, pattern: re.Pattern[str]) -> bool:
        for frame in page.frames:
            locator = frame.get_by_text(pattern).first
            try:
                if await locator.count() and await locator.is_visible():
                    return True
            except PlaywrightError:
                continue
        return False

    async def _upload_ready(self, page: Page) -> bool:
        if "/login" in page.url:
            return False
        for selector in FILE_INPUT_SELECTORS:
            try:
                if await page.locator(selector).count():
                    return True
            except PlaywrightError:
                continue
        return False

    async def _login_visible(self, page: Page) -> bool:
        if "/login" in page.url:
            return True
        try:
            if await page.locator('input[type="password"]').first.is_visible():
                return True
        except PlaywrightError:
            pass
        return await self._visible_text(page, LOGIN_RE)

    async def _wait_for_upload_page(self, page: Page, timeout_ms: int) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if page.is_closed():
                raise TikTokBrowserError("La ventana de Chromium se cerró.")
            if await self._login_visible(page):
                return False
            if await self._upload_ready(page):
                return True
            await asyncio.sleep(1)
        return False

    async def _dismiss_known_dialogs(self, page: Page) -> None:
        for _ in range(2):
            button = page.get_by_role("button", name=ACCEPT_BUTTON_RE).first
            try:
                if await button.count() and await button.is_visible():
                    await button.click(timeout=2_000)
                    await page.wait_for_timeout(500)
            except PlaywrightError:
                pass

    async def _safe_screenshot(self, page: Page, job_id: int) -> str | None:
        if page.is_closed():
            return None
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS_DIR / f"job-{job_id}-error-{int(time.time() * 1000)}.png"
        try:
            await page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            logger.warning("No se pudo guardar la captura de error para el job %s", job_id)
            return None

    async def shutdown(self) -> None:
        context = self._active_context
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


def _playwright_message(exc: PlaywrightError) -> str:
    text = str(exc)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return "Chromium no está instalado. Ejecuta setup.bat para instalarlo."
    if "ProcessSingleton" in text or "user data directory is already in use" in text:
        return (
            "El perfil de Chromium de TikTok está en uso. Cierra la ventana de TikTok que quedó "
            "abierta y vuelve a intentarlo."
        )
    if "has been closed" in text or "Target closed" in text:
        return "La ventana de Chromium se cerró durante la publicación."
    if "Timeout" in text:
        return "TikTok tardó demasiado en responder. Revisa la conexión e inténtalo de nuevo."
    return "No se pudo controlar el navegador de TikTok. Revisa la consola para más detalles."


tiktok_service = TikTokService()

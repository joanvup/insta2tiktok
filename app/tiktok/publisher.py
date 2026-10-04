from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .auth import TikTokAuth
from .chunking import plan_chunks
from .client import TikTokAPIError, TikTokClient
from .validator import validate
from ..config.settings import Settings, get_settings
from ..storage.database import ReelRepository
from ..storage.models import Reel, ReelStatus

log = logging.getLogger("insta2tiktok.tiktok.publisher")

MAX_TIKTOK_CAPTION_UTF16 = 2200


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _utf16_slice(text: str, max_units: int) -> str:
    raw = text.encode("utf-16-le")
    chunk = raw[: max_units * 2]
    # Evita partir un surrogate pair: truncamos hasta que sea decodificable
    while True:
        try:
            return chunk.decode("utf-16-le")
        except UnicodeDecodeError:
            if len(chunk) <= 2:
                return ""
            chunk = chunk[:-2]


class TikTokPublisher:
    """Publica un Reel descargado en TikTok vía Content Posting API v2.

    IMPORTANTE: Mientras la app de TikTok no pase la auditoría,
    los videos publicados vía Direct Post solo serán visibles como
    privados (SELF_ONLY), independientemente de privacy_level.
    """

    def __init__(
        self,
        auth: TikTokAuth | None = None,
        client: TikTokClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._auth = auth or TikTokAuth(self._settings)
        self._client = client or TikTokClient(self._auth, self._settings)

    def _build_caption(self, original_caption: str) -> str:
        prefix = self._settings.TIKTOK_CAPTION_PREFIX or ""
        suffix = self._settings.TIKTOK_CAPTION_SUFFIX or ""
        hashtags = self._settings.TIKTOK_CAPTION_HASHTAGS or ""

        parts = [p for p in [prefix, original_caption, hashtags, suffix] if p]
        full = " ".join(parts).strip()

        if _utf16_len(full) > MAX_TIKTOK_CAPTION_UTF16:
            overhead = _utf16_len(" ".join(p for p in [prefix, hashtags, suffix] if p) + " " * 3)
            budget = MAX_TIKTOK_CAPTION_UTF16 - overhead
            trimmed = _utf16_slice(original_caption, max(0, budget))
            parts2 = [p for p in [prefix, trimmed, hashtags, suffix] if p]
            full = " ".join(parts2).strip()

        result = _utf16_slice(full, MAX_TIKTOK_CAPTION_UTF16)
        return result

    def _validate_video(self, video_path: Path) -> str | None:
        if not video_path.exists():
            return f"Archivo no encontrado: {video_path}"

        ext_msg = validate(
            video_path,
            max_duration=self._client.max_duration_seconds(),
            ffprobe_bin=self._settings.FFPROBE_PATH or None,
        )
        return ext_msg

    def publish(self, reel: Reel) -> bool:
        video_path = Path(reel.video_path) if reel.video_path else Path("")
        log.info("Publicando Reel %s en TikTok...", reel.shortcode)

        validation_error = self._validate_video(video_path)
        if validation_error:
            log.error("Validacion fallo: %s", validation_error)
            reel.status = ReelStatus.FAILED.value
            reel.fail_reason = validation_error
            ReelRepository.update(reel)
            return False

        reel.status = ReelStatus.PROCESSING.value
        ReelRepository.update(reel)

        try:
            caption = self._build_caption(reel.caption or "")
            privacy = self._client.resolve_privacy_level(self._settings.TIKTOK_PRIVACY)

            plan = plan_chunks(video_path.stat().st_size)

            init_data = self._client.init_upload(video_path, caption, privacy, plan)
            upload_url = init_data.get("upload_url", "")
            publish_id = init_data.get("publish_id", "")

            if not upload_url:
                raise TikTokAPIError("No se recibio upload_url de TikTok")

            if not publish_id:
                log.warning("No se recibio publish_id, no se podra verificar el estado")

            self._client.upload_video_chunks(upload_url, video_path, plan)

            if publish_id:
                result = self._client.check_publish_status(publish_id)
                reel.tiktok_publish_id = publish_id

                post_ids = (result or {}).get("publicaly_available_post_id") or []
                if post_ids:
                    log.info("Post moderado y publico: %s", post_ids)

            reel.status = ReelStatus.PUBLISHED.value
            reel.published_at = datetime.now(timezone.utc)
            reel.fail_reason = None
            ReelRepository.update(reel)
            log.info("Reel %s publicado exitosamente en TikTok", reel.shortcode)
            return True

        except TikTokAPIError as exc:
            retryable = getattr(exc, "retryable", False)
            if "unaudited_client" in exc.code:
                log.error(
                    "Cliente no auditado: solo se puede publicar como privado (SELF_ONLY). "
                    "Solucion: usa TIKTOK_PRIVACY=SELF_ONLY o solicita la revision en "
                    "developers.tiktok.com/application/content-posting-api."
                )

            log.error("Error de API TikTok publicando %s: %s (code=%s)", reel.shortcode, exc, exc.code)
            reel.status = ReelStatus.FAILED.value
            reel.fail_reason = str(exc)
            reel.retry_count += 1
            ReelRepository.update(reel)
            return False

        except Exception as exc:  # noqa: BLE001
            log.error("Error inesperado publicando %s: %s", reel.shortcode, exc)
            reel.status = ReelStatus.FAILED.value
            reel.fail_reason = str(exc)
            reel.retry_count += 1
            ReelRepository.update(reel)
            return False

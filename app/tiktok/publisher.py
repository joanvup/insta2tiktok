from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .auth import TikTokAuth
from .client import TikTokAPIError, TikTokClient
from ..config.settings import Settings, get_settings
from ..storage.database import ReelRepository
from ..storage.models import Reel, ReelStatus

log = logging.getLogger("insta2tiktok.tiktok.publisher")

MAX_TIKTOK_CAPTION = 2200


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
        prefix = self._settings.TIKTOK_CAPTION_PREFIX
        suffix = self._settings.TIKTOK_CAPTION_SUFFIX
        hashtags = self._settings.TIKTOK_CAPTION_HASHTAGS

        parts = []
        if prefix:
            parts.append(prefix)
        parts.append(original_caption)
        if hashtags:
            parts.append(hashtags)
        if suffix:
            parts.append(suffix)

        full = " ".join(parts).strip()
        if len(full) > MAX_TIKTOK_CAPTION:
            overflow = len(full) - MAX_TIKTOK_CAPTION
            trimmed_caption = original_caption[: max(0, len(original_caption) - overflow)]
            parts_trimmed = []
            if prefix:
                parts_trimmed.append(prefix)
            parts_trimmed.append(trimmed_caption)
            if hashtags:
                parts_trimmed.append(hashtags)
            if suffix:
                parts_trimmed.append(suffix)
            full = " ".join(parts_trimmed).strip()

        return full[:MAX_TIKTOK_CAPTION]

    def _validate_video(self, video_path: Path) -> str | None:
        if not video_path.exists():
            return f"Archivo no encontrado: {video_path}"

        size_mb = video_path.stat().st_size / 1_048_576
        if size_mb > 4096:
            return f"Video demasiado grande: {size_mb:.1f} MB (máx 4 GB)"

        if video_path.suffix.lower() not in {".mp4", ".webm", ".mov"}:
            return f"Formato no soportado: {video_path.suffix}"

        return None

    def publish(self, reel: Reel) -> bool:
        video_path = Path(reel.video_path)
        log.info("Publicando Reel %s en TikTok...", reel.shortcode)

        validation_error = self._validate_video(video_path)
        if validation_error:
            log.error("Validación falló: %s", validation_error)
            reel.status = ReelStatus.FAILED.value
            reel.fail_reason = validation_error
            ReelRepository.update(reel)
            return False

        reel.status = ReelStatus.PROCESSING.value
        ReelRepository.update(reel)

        try:
            caption = self._build_caption(reel.caption or "")
            privacy = self._settings.TIKTOK_PRIVACY

            init_data = self._client.init_upload(video_path, caption, privacy)
            upload_url = init_data.get("upload_url", "")
            publish_id = init_data.get("publish_id", "")

            if not upload_url:
                raise TikTokAPIError("No se recibió upload_url de TikTok")

            self._client.upload_video_chunks(upload_url, video_path)

            if publish_id:
                result = self._client.check_publish_status(publish_id)
                reel.tiktok_publish_id = publish_id
            else:
                log.warning("No se recibió publish_id, no se puede verificar estado")

            reel.status = ReelStatus.PUBLISHED.value
            reel.published_at = datetime.now(timezone.utc)
            reel.fail_reason = None
            ReelRepository.update(reel)
            log.info("Reel %s publicado exitosamente en TikTok", reel.shortcode)
            return True

        except TikTokAPIError as exc:
            log.error("Error de API TikTok publicando %s: %s", reel.shortcode, exc)
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

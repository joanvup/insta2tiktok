from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from ..config.settings import Settings, get_settings
from ..dedup.service import DedupService
from ..instagram.extractor import InstagramExtractor, ReelInfo
from ..storage.database import ReelRepository
from ..storage.models import Reel, ReelStatus
from ..tiktok.auth import TikTokAuth
from ..tiktok.client import TikTokClient
from ..tiktok.publisher import TikTokPublisher

log = logging.getLogger("insta2tiktok.scheduler")


@dataclass
class RunSummary:
    discovered: int = 0
    downloaded: int = 0
    skipped_duplicate: int = 0
    published: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        settings: Settings | None = None,
        dry_run: bool = False,
    ) -> None:
        self._settings = settings or get_settings()
        self._dry_run = dry_run
        self._dedup = DedupService(self._settings)
        self._extractor = InstagramExtractor(self._settings)
        self._auth = TikTokAuth(self._settings)
        self._client = TikTokClient(self._auth, self._settings)
        self._publisher = TikTokPublisher(self._auth, self._client, self._settings)

    def run(self) -> RunSummary:
        summary = RunSummary()
        log.info("=== Iniciando ciclo de publicación %s ===", datetime.now(timezone.utc).isoformat())

        daily_published = ReelRepository.count_published_today()
        if daily_published >= self._settings.MAX_DAILY_PUBLISHES:
            log.warning(
                "Límite diario alcanzado (%d/%d). No se publicará nada.",
                daily_published,
                self._settings.MAX_DAILY_PUBLISHES,
            )
            return summary

        remaining_today = self._settings.MAX_DAILY_PUBLISHES - daily_published
        limit_this_run = min(self._settings.PUBLISH_LIMIT_PER_RUN, remaining_today)

        reels_info = self._extractor.fetch_reels()
        summary.discovered = len(reels_info)

        for info in reels_info:
            is_dup, reason = self._dedup.check(info.shortcode)
            if is_dup:
                log.info("Omitiendo %s: duplicado por %s", info.shortcode, reason)
                summary.skipped_duplicate += 1
                existing = ReelRepository.get_by_shortcode(info.shortcode)
                if existing and existing.status == ReelStatus.DISCOVERED.value:
                    existing.status = ReelStatus.SKIPPED_DUPLICATE.value
                    ReelRepository.update(existing)
                continue

            existing = ReelRepository.get_by_shortcode(info.shortcode)
            if existing is None:
                reel = Reel(
                    shortcode=info.shortcode,
                    video_url=info.video_url,
                    caption=info.caption,
                    instagram_timestamp=info.timestamp,
                    duration=info.duration,
                    status=ReelStatus.DISCOVERED.value,
                )
                ReelRepository.add(reel)
            else:
                reel = existing

            if reel.status in {ReelStatus.PUBLISHED.value, ReelStatus.PROCESSING.value}:
                summary.skipped_duplicate += 1
                continue

            if reel.status == ReelStatus.DISCOVERED.value:
                try:
                    if self._dry_run:
                        log.info("[DRY-RUN] Descargaría: %s", info.shortcode)
                    else:
                        video_path = self._extractor.download_video(info)
                        vhash, phash = self._dedup.compute_hashes(video_path)

                        is_dup2, reason2 = self._dedup.check(info.shortcode, video_path)
                        if is_dup2:
                            log.info("Omitiendo %s tras descarga: duplicado por %s", info.shortcode, reason2)
                            reel.status = ReelStatus.SKIPPED_DUPLICATE.value
                            reel.video_path = str(video_path)
                            ReelRepository.update(reel)
                            summary.skipped_duplicate += 1
                            continue

                        reel.status = ReelStatus.DOWNLOADED.value
                        reel.video_path = str(video_path)
                        reel.video_hash = vhash
                        reel.perceptual_hash = phash
                        ReelRepository.update(reel)
                        summary.downloaded += 1

                except Exception as exc:  # noqa: BLE001
                    log.error("Error descargando %s: %s", info.shortcode, exc)
                    reel.status = ReelStatus.FAILED.value
                    reel.fail_reason = str(exc)
                    ReelRepository.update(reel)
                    summary.failed += 1
                    summary.errors.append(f"Descarga {info.shortcode}: {exc}")
                    continue

        if self._dry_run:
            log.info("[DRY-RUN] Publicaría hasta %d Reels", limit_this_run)
            return summary

        pending = ReelRepository.get_pending()
        log.info(
            "Reels pendientes de publicar: %d (límite esta ejecución: %d)",
            len(pending),
            limit_this_run,
        )

        published_this_run = 0
        for reel in pending:
            if published_this_run >= limit_this_run:
                break
            ok = self._publisher.publish(reel)
            if ok:
                published_this_run += 1
                summary.published += 1
                self._cleanup_video(reel)
            else:
                summary.failed += 1
                summary.errors.append(f"Publicación {reel.shortcode}: {reel.fail_reason}")

        self._cleanup_old_videos()
        self._log_summary(summary)
        return summary

    def retry_failed(self) -> RunSummary:
        summary = RunSummary()
        failed = ReelRepository.get_failed_retriable(self._settings.MAX_RETRY_COUNT)
        log.info("Reintentando %d Reels fallidos...", len(failed))

        for reel in failed:
            if reel.video_path and Path(reel.video_path).exists():
                reel.status = ReelStatus.DOWNLOADED.value
                ReelRepository.update(reel)
                ok = self._publisher.publish(reel)
                if ok:
                    summary.published += 1
                    self._cleanup_video(reel)
                else:
                    summary.failed += 1
            else:
                log.warning("Reel %s: video no disponible para reintento", reel.shortcode)
                summary.failed += 1

        return summary

    def _cleanup_video(self, reel: Reel) -> None:
        if reel.video_path:
            p = Path(reel.video_path)
            if p.exists():
                try:
                    p.unlink()
                    log.debug("Video eliminado: %s", p.name)
                except OSError as exc:
                    log.warning("No se pudo eliminar %s: %s", p.name, exc)
                reel.video_path = None
                ReelRepository.update(reel)

    def _cleanup_old_videos(self) -> None:
        old = ReelRepository.get_published_old_videos(self._settings.VIDEO_RETENTION_HOURS)
        for reel in old:
            self._cleanup_video(reel)

    def _log_summary(self, summary: RunSummary) -> None:
        log.info(
            "=== Resumen: descubiertos=%d descargados=%d duplicados_omitidos=%d "
            "publicados=%d fallidos=%d ===",
            summary.discovered,
            summary.downloaded,
            summary.skipped_duplicate,
            summary.published,
            summary.failed,
        )
        if summary.errors:
            for err in summary.errors:
                log.error("  ✗ %s", err)


def run_cycle(dry_run: bool = False) -> RunSummary:
    orchestrator = Orchestrator(dry_run=dry_run)
    return orchestrator.run()

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple

from ..config.settings import get_settings
from ..storage.database import ReelRepository
from ..storage.models import Reel, ReelStatus

log = logging.getLogger("insta2tiktok.dedup")


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def perceptual_hash_of_video(path: Path) -> Optional[str]:
    """Extrae hash perceptual del primer fotograma del video.

    Solo disponible si ENABLE_PERCEPTUAL_HASH=true y las dependencias
    'imagehash' y 'Pillow' están instaladas.
    """
    try:
        import av  # type: ignore
        import imagehash  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        log.warning("imagehash/av/Pillow no instalados; hash perceptual omitido")
        return None

    try:
        with av.open(str(path)) as container:
            for frame in container.decode(video=0):
                img = frame.to_image()
                phash = str(imagehash.phash(img))
                return phash
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo calcular hash perceptual para %s: %s", path.name, exc)
        return None


class DuplicateReason(str):
    SHORTCODE = "shortcode"
    FILE_HASH = "file_hash"
    PERCEPTUAL_HASH = "perceptual_hash"
    NOT_DUPLICATE = ""


class DedupService:
    def __init__(self) -> None:
        self._settings = get_settings()

    def check(
        self,
        shortcode: str,
        video_path: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """Devuelve (es_duplicado, motivo)."""

        existing = ReelRepository.get_by_shortcode(shortcode)
        if existing and existing.status in {
            ReelStatus.PUBLISHED.value,
            ReelStatus.PROCESSING.value,
            ReelStatus.DOWNLOADED.value,
            ReelStatus.SKIPPED_DUPLICATE.value,
        }:
            log.info("Duplicado por shortcode: %s (status=%s)", shortcode, existing.status)
            return True, DuplicateReason.SHORTCODE

        if video_path and video_path.exists():
            vhash = sha256_of_file(video_path)
            dup = ReelRepository.get_by_hash(vhash)
            if dup and dup.shortcode != shortcode:
                log.info(
                    "Duplicado por hash SHA-256: %s ya existe como shortcode=%s",
                    shortcode,
                    dup.shortcode,
                )
                return True, DuplicateReason.FILE_HASH

            if self._settings.ENABLE_PERCEPTUAL_HASH:
                phash = perceptual_hash_of_video(video_path)
                if phash:
                    dup_p = ReelRepository.get_by_perceptual_hash(phash)
                    if dup_p and dup_p.shortcode != shortcode:
                        log.info(
                            "Duplicado por hash perceptual: %s ~= shortcode=%s",
                            shortcode,
                            dup_p.shortcode,
                        )
                        return True, DuplicateReason.PERCEPTUAL_HASH

        return False, DuplicateReason.NOT_DUPLICATE

    def compute_hashes(self, video_path: Path) -> Tuple[str, Optional[str]]:
        vhash = sha256_of_file(video_path)
        phash = perceptual_hash_of_video(video_path) if self._settings.ENABLE_PERCEPTUAL_HASH else None
        return vhash, phash

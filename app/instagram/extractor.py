from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Protocol

from ..config.settings import Settings, get_settings

log = logging.getLogger("insta2tiktok.instagram")


@dataclass
class ReelInfo:
    shortcode: str
    video_url: str
    timestamp: datetime
    caption: str
    hashtags: List[str]
    duration: Optional[float]


class ReelExtractorInterface(Protocol):
    def fetch_reels(self, username: str, count: int) -> List[ReelInfo]: ...
    def download_video(self, reel: ReelInfo, target_dir: Path) -> Path: ...


class InstagramExtractor:
    """Extracción de Reels usando instaloader.

    Se eligió instaloader por:
    - No requiere API oficial de Instagram.
    - Soporte nativo para sesiones (cookies) permitiendo evitar rate-limiting.
    - Comunidad activa de mantenimiento.

    ADVERTENCIA: El scraping de Instagram puede romper sus TdS y puede
    dejar de funcionar en cualquier momento sin previo aviso.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._loader = self._create_loader()

    def _create_loader(self):
        from instaloader import Instaloader

        loader = Instaloader(
            download_videos=False,
            download_comments=False,
            save_metadata=False,
            quiet=True,
            max_connection_attempts=3,
        )
        session_file = self._settings.INSTAGRAM_SESSION_FILE
        if session_file and Path(session_file).exists():
            try:
                loader.load_session_from_file(username="", filename=session_file)
                log.info("Sesión de Instagram cargada desde %s", session_file)
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo cargar sesión de Instagram: %s", exc)
        return loader

    def _delay(self) -> None:
        delay = random.uniform(
            self._settings.INSTAGRAM_REQUEST_DELAY_MIN,
            self._settings.INSTAGRAM_REQUEST_DELAY_MAX,
        )
        log.debug("Esperando %.1fs entre peticiones", delay)
        time.sleep(delay)

    def fetch_reels(self, username: str | None = None, count: int | None = None) -> List[ReelInfo]:
        from instaloader import Profile
        from instaloader.exceptions import (
            ProfileNotExistsException,
            PrivateProfileNotFollowedException,
            ConnectionException,
            QueryReturnedBadRequestException,
        )

        username = username or self._settings.INSTAGRAM_USERNAME
        count = count or self._settings.INSTAGRAM_REELS_COUNT

        log.info("Obteniendo hasta %d Reels de @%s", count, username)

        try:
            profile = Profile.from_username(self._loader.context, username)
        except ProfileNotExistsException:
            log.error("La cuenta @%s no existe", username)
            return []
        except PrivateProfileNotFollowedException:
            log.error("La cuenta @%s es privada", username)
            return []
        except ConnectionException as exc:
            log.error("Error de conexión al obtener perfil @%s: %s", username, exc)
            return []

        if profile.is_private:
            log.error("La cuenta @%s es privada, no se pueden obtener Reels", username)
            return []

        reels: List[ReelInfo] = []
        attempt_errors = 0
        max_errors = 5

        try:
            for post in profile.get_posts():
                if len(reels) >= count:
                    break

                try:
                    if not post.is_video:
                        continue

                    if not hasattr(post, "product_type") or post.product_type != "clips":
                        continue

                    if not post.video_url:
                        continue

                    caption = post.caption or ""
                    hashtags = [
                        tag for tag in caption.split() if tag.startswith("#")
                    ]

                    reel = ReelInfo(
                        shortcode=post.shortcode,
                        video_url=post.video_url,
                        timestamp=post.date_utc,
                        caption=caption,
                        hashtags=hashtags,
                        duration=getattr(post, "video_duration", None),
                    )
                    reels.append(reel)
                    log.info(
                        "Reel encontrado: %s (duración=%.1fs)",
                        reel.shortcode,
                        reel.duration or 0,
                    )

                except Exception as exc:  # noqa: BLE001
                    attempt_errors += 1
                    log.warning("Error procesando post: %s", exc)
                    if attempt_errors >= max_errors:
                        log.error("Demasiados errores, deteniendo búsqueda")
                        break

                self._delay()

        except QueryReturnedBadRequestException as exc:
            log.error("Instagram devolvió Bad Request (posible rate-limit): %s", exc)
        except ConnectionException as exc:
            log.error("Error de conexión durante iteración: %s", exc)

        log.info("Total de Reels obtenidos: %d", len(reels))
        return reels

    def download_video(self, reel: ReelInfo, target_dir: Path | None = None) -> Path:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        target_dir = target_dir or self._settings.WORKDIR
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{reel.shortcode}.mp4"

        if target.exists():
            log.debug("Video ya existe en disco: %s", target)
            return target

        log.info("Descargando video %s → %s", reel.shortcode, target.name)

        session = requests.Session()
        retries = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries))

        resp = session.get(reel.video_url, stream=True, timeout=60)
        resp.raise_for_status()

        with target.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

        log.info("Video descargado: %s (%.2f MB)", target.name, target.stat().st_size / 1_048_576)
        return target

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Optional, Sequence

from sqlmodel import Session, SQLModel, create_engine, select

from .models import Reel, ReelStatus

log = logging.getLogger("insta2tiktok.storage")

_engine = None


def _get_engine(db_path: Path | None = None):
    global _engine
    if _engine is None:
        if db_path is None:
            from ..config.settings import get_settings
            db_path = get_settings().DB_PATH
        url = f"sqlite:///{db_path.absolute()}"
        _engine = create_engine(url, echo=False)
        log.debug("Motor SQLite inicializado: %s", url)
    return _engine


def init_db(db_path: Path | None = None) -> None:
    engine = _get_engine(db_path)
    SQLModel.metadata.create_all(engine)
    log.info("Base de datos inicializada")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    with Session(_get_engine()) as session:
        yield session


def reset_engine() -> None:
    global _engine
    _engine = None


class ReelRepository:
    @staticmethod
    def add(reel: Reel) -> Reel:
        with get_session() as s:
            s.add(reel)
            s.commit()
            s.refresh(reel)
            log.debug("Reel agregado: shortcode=%s status=%s", reel.shortcode, reel.status)
        return reel

    @staticmethod
    def get_by_shortcode(shortcode: str) -> Optional[Reel]:
        with get_session() as s:
            return s.exec(select(Reel).where(Reel.shortcode == shortcode)).first()

    @staticmethod
    def get_by_hash(video_hash: str) -> Optional[Reel]:
        with get_session() as s:
            return s.exec(select(Reel).where(Reel.video_hash == video_hash)).first()

    @staticmethod
    def get_by_perceptual_hash(phash: str) -> Optional[Reel]:
        with get_session() as s:
            return s.exec(select(Reel).where(Reel.perceptual_hash == phash)).first()

    @staticmethod
    def update(reel: Reel) -> None:
        reel.updated_at = datetime.now(timezone.utc)
        with get_session() as s:
            s.merge(reel)
            s.commit()
            log.debug("Reel actualizado: shortcode=%s status=%s", reel.shortcode, reel.status)

    @staticmethod
    def get_pending() -> Sequence[Reel]:
        with get_session() as s:
            return list(s.exec(
                select(Reel).where(
                    Reel.status.in_([ReelStatus.DOWNLOADED.value, ReelStatus.PROCESSING.value])
                )
            ).all())

    @staticmethod
    def get_failed_retriable(max_retries: int) -> Sequence[Reel]:
        with get_session() as s:
            return list(s.exec(
                select(Reel).where(
                    Reel.status == ReelStatus.FAILED.value,
                    Reel.retry_count < max_retries,
                )
            ).all())

    @staticmethod
    def count_published_today() -> int:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with get_session() as s:
            results = s.exec(
                select(Reel).where(
                    Reel.status == ReelStatus.PUBLISHED.value,
                    Reel.published_at >= today_start,
                )
            ).all()
            return len(list(results))

    @staticmethod
    def get_recent(limit: int = 20) -> Sequence[Reel]:
        with get_session() as s:
            return list(s.exec(
                select(Reel).order_by(Reel.created_at.desc()).limit(limit)
            ).all())

    @staticmethod
    def get_published_old_videos(retention_hours: int) -> Sequence[Reel]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        with get_session() as s:
            return list(s.exec(
                select(Reel).where(
                    Reel.status == ReelStatus.PUBLISHED.value,
                    Reel.published_at < cutoff,
                    Reel.video_path.isnot(None),
                )
            ).all())

    @staticmethod
    def reset_reel(shortcode: str) -> Optional[Reel]:
        reel = ReelRepository.get_by_shortcode(shortcode)
        if reel is None:
            return None
        reel.status = ReelStatus.DOWNLOADED.value
        reel.retry_count = 0
        reel.fail_reason = None
        reel.tiktok_publish_id = None
        ReelRepository.update(reel)
        return reel

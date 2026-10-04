from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class ReelStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    DOWNLOADED = "downloaded"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class Reel(SQLModel, table=True):
    __tablename__ = "reels"

    id: Optional[int] = Field(default=None, primary_key=True)
    shortcode: str = Field(index=True, unique=True)
    video_url: Optional[str] = Field(default=None)
    video_path: Optional[str] = Field(default=None)
    video_hash: Optional[str] = Field(default=None, index=True)
    perceptual_hash: Optional[str] = Field(default=None, index=True)
    caption: Optional[str] = Field(default=None)
    instagram_timestamp: Optional[datetime] = Field(default=None)
    duration: Optional[float] = Field(default=None)
    status: str = Field(default=ReelStatus.DISCOVERED.value, index=True)
    fail_reason: Optional[str] = Field(default=None)
    retry_count: int = Field(default=0)
    tiktok_publish_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: Optional[datetime] = Field(default=None)

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "https://example.com/callback")

from app.config.settings import Settings, get_settings
from app.dedup.service import DedupService, sha256_of_file
from app.storage.database import ReelRepository, init_db, reset_engine
from app.storage.models import Reel, ReelStatus


@pytest.fixture(autouse=True)
def clean_db(tmp_path):
    reset_engine()
    db_path = tmp_path / "test.db"
    os.environ["DB_PATH"] = str(db_path)
    os.environ["WORKDIR"] = str(tmp_path / "workdir")
    get_settings.cache_clear()
    init_db(db_path)
    yield
    get_settings.cache_clear()
    reset_engine()


class TestSHA256:
    def test_computes_correct_hash(self, tmp_path):
        p = tmp_path / "sample.bin"
        p.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert sha256_of_file(p) == expected

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert sha256_of_file(a) != sha256_of_file(b)

    def test_same_content_same_hash(self, tmp_path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"xxx")
        b.write_bytes(b"xxx")
        assert sha256_of_file(a) == sha256_of_file(b)


class TestDedupService:
    def test_not_duplicate_when_empty(self, tmp_path):
        dedup = DedupService()
        is_dup, reason = dedup.check("abc123")
        assert is_dup is False

    def test_duplicate_by_shortcode(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"video_data")
        reel = Reel(
            shortcode="dup_test",
            video_path=str(video),
            video_hash=sha256_of_file(video),
            status=ReelStatus.PUBLISHED.value,
        )
        ReelRepository.add(reel)
        dedup = DedupService()
        is_dup, reason = dedup.check("dup_test")
        assert is_dup is True
        assert reason == "shortcode"

    def test_duplicate_by_hash(self, tmp_path):
        video = tmp_path / "original.mp4"
        video.write_bytes(b"same_content")
        reel = Reel(
            shortcode="orig",
            video_path=str(video),
            video_hash=sha256_of_file(video),
            status=ReelStatus.DOWNLOADED.value,
        )
        ReelRepository.add(reel)

        copy = tmp_path / "copy.mp4"
        copy.write_bytes(b"same_content")

        dedup = DedupService()
        is_dup, reason = dedup.check("different_shortcode", video_path=copy)
        assert is_dup is True
        assert reason == "file_hash"


class TestReelRepository:
    def test_add_and_get(self, tmp_path):
        reel = Reel(shortcode="test1", status=ReelStatus.DISCOVERED.value)
        saved = ReelRepository.add(reel)
        assert saved.id is not None
        found = ReelRepository.get_by_shortcode("test1")
        assert found is not None
        assert found.shortcode == "test1"

    def test_update_status(self, tmp_path):
        reel = Reel(shortcode="upd1", status=ReelStatus.DISCOVERED.value)
        ReelRepository.add(reel)
        reel.status = ReelStatus.DOWNLOADED.value
        ReelRepository.update(reel)
        found = ReelRepository.get_by_shortcode("upd1")
        assert found.status == ReelStatus.DOWNLOADED.value

    def test_get_pending(self, tmp_path):
        r1 = Reel(shortcode="p1", status=ReelStatus.DOWNLOADED.value)
        r2 = Reel(shortcode="p2", status=ReelStatus.PUBLISHED.value)
        r3 = Reel(shortcode="p3", status=ReelStatus.PROCESSING.value)
        ReelRepository.add(r1)
        ReelRepository.add(r2)
        ReelRepository.add(r3)
        pending = ReelRepository.get_pending()
        shortcodes = {r.shortcode for r in pending}
        assert "p1" in shortcodes
        assert "p3" in shortcodes
        assert "p2" not in shortcodes

    def test_reset_reel(self, tmp_path):
        reel = Reel(shortcode="rst1", status=ReelStatus.FAILED.value, retry_count=3, fail_reason="err")
        ReelRepository.add(reel)
        reset = ReelRepository.reset_reel("rst1")
        assert reset is not None
        assert reset.status == ReelStatus.DOWNLOADED.value
        assert reset.retry_count == 0
        assert reset.fail_reason is None

    def test_count_published_today(self, tmp_path):
        from datetime import datetime, timezone

        reel = Reel(
            shortcode="pub1",
            status=ReelStatus.PUBLISHED.value,
            published_at=datetime.now(timezone.utc),
        )
        ReelRepository.add(reel)
        assert ReelRepository.count_published_today() >= 1

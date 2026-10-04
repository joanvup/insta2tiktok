import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "http://localhost:8080/callback")

from app.tiktok.publisher import TikTokPublisher, MAX_TIKTOK_CAPTION


class TestCaptionBuild:
    def test_basic_caption(self):
        pub = TikTokPublisher.__new__(TikTokPublisher)
        pub._settings = MagicMock()
        pub._settings.TIKTOK_CAPTION_PREFIX = ""
        pub._settings.TIKTOK_CAPTION_SUFFIX = ""
        pub._settings.TIKTOK_CAPTION_HASHTAGS = ""
        assert pub._build_caption("Hello") == "Hello"

    def test_caption_with_prefix_suffix(self):
        pub = TikTokPublisher.__new__(TikTokPublisher)
        pub._settings = MagicMock()
        pub._settings.TIKTOK_CAPTION_PREFIX = "[Repost]"
        pub._settings.TIKTOK_CAPTION_SUFFIX = "- vía IG"
        pub._settings.TIKTOK_CAPTION_HASHTAGS = "#repost"
        result = pub._build_caption("Mi video")
        assert result.startswith("[Repost]")
        assert "#repost" in result
        assert "- vía IG" in result

    def test_caption_truncation(self):
        pub = TikTokPublisher.__new__(TikTokPublisher)
        pub._settings = MagicMock()
        pub._settings.TIKTOK_CAPTION_PREFIX = ""
        pub._settings.TIKTOK_CAPTION_SUFFIX = ""
        pub._settings.TIKTOK_CAPTION_HASHTAGS = ""
        long_text = "x" * 3000
        result = pub._build_caption(long_text)
        assert len(result) <= MAX_TIKTOK_CAPTION

    def test_caption_with_everything_truncated(self):
        pub = TikTokPublisher.__new__(TikTokPublisher)
        pub._settings = MagicMock()
        pub._settings.TIKTOK_CAPTION_PREFIX = "PREFIX"
        pub._settings.TIKTOK_CAPTION_SUFFIX = "SUFFIX"
        pub._settings.TIKTOK_CAPTION_HASHTAGS = "#tag1 #tag2"
        long_text = "a" * 3000
        result = pub._build_caption(long_text)
        assert len(result) <= MAX_TIKTOK_CAPTION
        assert "PREFIX" in result


class TestVideoValidation:
    def test_missing_file(self, tmp_path):
        pub = TikTokPublisher.__new__(TikTokPublisher)
        err = pub._validate_video(tmp_path / "nonexistent.mp4")
        assert err is not None
        assert "no encontrado" in err.lower() or "not found" in err.lower()

    def test_valid_mp4(self, tmp_path):
        v = tmp_path / "test.mp4"
        v.write_bytes(b"x" * 100)
        pub = TikTokPublisher.__new__(TikTokPublisher)
        assert pub._validate_video(v) is None

    def test_invalid_extension(self, tmp_path):
        v = tmp_path / "test.avi"
        v.write_bytes(b"x" * 100)
        pub = TikTokPublisher.__new__(TikTokPublisher)
        err = pub._validate_video(v)
        assert err is not None
        assert "no soportado" in err.lower()

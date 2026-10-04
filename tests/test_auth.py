import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "http://localhost:8080/callback")

from app.tiktok.auth import TikTokAuth


class TestTokenRefresh:
    def test_refresh_called_when_expired(self, tmp_path):
        token_path = tmp_path / "token.json"
        expired_token = {
            "access_token": "old_token",
            "refresh_token": "refresh_123",
            "open_id": "openid_1",
            "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "refresh_expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        }
        token_path.write_text(json.dumps(expired_token))

        settings = MagicMock()
        settings.TIKTOK_TOKEN_PATH = token_path
        settings.TIKTOK_CLIENT_KEY = "key"
        settings.TIKTOK_CLIENT_SECRET = "secret"

        auth = TikTokAuth(settings)

        refresh_response = MagicMock()
        refresh_response.json.return_value = {
            "access_token": "new_token",
            "refresh_token": "new_refresh",
            "expires_in": 86400,
        }
        refresh_response.raise_for_status = MagicMock()

        with patch("app.tiktok.auth.requests.post", return_value=refresh_response):
            token = auth.get_access_token()

        assert token == "new_token"
        saved = json.loads(token_path.read_text())
        assert saved["access_token"] == "new_token"

    def test_no_refresh_when_valid(self, tmp_path):
        token_path = tmp_path / "token.json"
        valid_token = {
            "access_token": "valid_token",
            "refresh_token": "refresh_123",
            "open_id": "openid_1",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
            "refresh_expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        }
        token_path.write_text(json.dumps(valid_token))

        settings = MagicMock()
        settings.TIKTOK_TOKEN_PATH = token_path
        settings.TIKTOK_CLIENT_KEY = "key"
        settings.TIKTOK_CLIENT_SECRET = "secret"

        auth = TikTokAuth(settings)
        token = auth.get_access_token()
        assert token == "valid_token"

    def test_error_when_no_token_file(self, tmp_path):
        settings = MagicMock()
        settings.TIKTOK_TOKEN_PATH = tmp_path / "nonexistent.json"

        auth = TikTokAuth(settings)
        with pytest.raises(RuntimeError, match="No hay token"):
            auth.get_access_token()

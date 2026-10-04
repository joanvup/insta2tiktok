import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "https://example.com/callback")

from app.tiktok.client import TikTokClient, TikTokAPIError
from app.tiktok.publisher import (
    TikTokPublisher,
    MAX_TIKTOK_CAPTION_UTF16,
    _utf16_len,
    _utf16_slice,
)


def _publisher():
    pub = TikTokPublisher.__new__(TikTokPublisher)
    pub._settings = MagicMock()
    pub._settings.TIKTOK_CAPTION_PREFIX = ""
    pub._settings.TIKTOK_CAPTION_SUFFIX = ""
    pub._settings.TIKTOK_CAPTION_HASHTAGS = ""
    pub._settings.FFPROBE_PATH = "ffprobe"
    pub._client = MagicMock()
    pub._client.max_duration_seconds.return_value = 600
    return pub


class TestUtf16:
    def test_ascii(self):
        assert _utf16_len("hello") == 5

    def test_emoji_cuenta_doble(self):
        assert _utf16_len("\U0001F3AC") == 2

    def test_slice_no_rompe_emoji(self):
        text = "ab\U0001F3ACcd"
        result = _utf16_slice(text, 3)
        assert _utf16_len(result) <= 3
        result.encode("utf-16-le")


class TestCaptionBuild:
    def test_basic(self):
        assert _publisher()._build_caption("Hello") == "Hello"

    def test_con_prefijo_sufijo(self):
        pub = _publisher()
        pub._settings.TIKTOK_CAPTION_PREFIX = "[Repost]"
        pub._settings.TIKTOK_CAPTION_SUFFIX = "- via IG"
        pub._settings.TIKTOK_CAPTION_HASHTAGS = "#repost"
        result = pub._build_caption("Mi video")
        assert result.startswith("[Repost]")
        assert "#repost" in result
        assert "- via IG" in result

    def test_recorta_en_runes_utf16(self):
        pub = _publisher()
        result = pub._build_caption("x" * 3000)
        assert _utf16_len(result) <= MAX_TIKTOK_CAPTION_UTF16

    def test_emoji_no_rompe_limite(self):
        pub = _publisher()
        result = pub._build_caption("\U0001F3AC" * 1500)
        assert _utf16_len(result) <= MAX_TIKTOK_CAPTION_UTF16
        result.encode("utf-16-le")

    def test_preserva_prefijo_al_recortar(self):
        pub = _publisher()
        pub._settings.TIKTOK_CAPTION_PREFIX = "PREFIX"
        result = pub._build_caption("a" * 3000)
        assert "PREFIX" in result


class TestResolvePrivacy:
    def _client(self, options):
        client = TikTokClient.__new__(TikTokClient)
        client._settings = MagicMock()
        client.query_creator_info = MagicMock(return_value={"privacy_level_options": options})
        return client

    def test_respeta_opcion_permitida(self):
        client = self._client(["SELF_ONLY", "PUBLIC_TO_EVERYONE"])
        assert client.resolve_privacy_level("PUBLIC_TO_EVERYONE") == "PUBLIC_TO_EVERYONE"

    def test_cae_a_self_only(self):
        client = self._client(["SELF_ONLY"])
        assert client.resolve_privacy_level("PUBLIC_TO_EVERYONE") == "SELF_ONLY"

    def test_sin_opciones_devuelve_lo_pedido(self):
        client = self._client([])
        assert client.resolve_privacy_level("SELF_ONLY") == "SELF_ONLY"

    def test_error_api_devuelve_lo_pedido(self):
        client = TikTokClient.__new__(TikTokClient)
        client._settings = MagicMock()

        def boom(*a, **k):
            raise TikTokAPIError("x")

        client.query_creator_info = boom
        assert client.resolve_privacy_level("SELF_ONLY") == "SELF_ONLY"


class TestPublishStatusHandling:
    def _client_with(self, statuses):
        client = TikTokClient.__new__(TikTokClient)
        client._settings = MagicMock()
        client._settings.TIKTOK_MAX_VIDEO_DURATION_SEC = 600
        client._auth = MagicMock()
        client._auth.get_access_token.return_value = "tok"
        client._creator_info = None

        responses = []
        for s in statuses:
            r = MagicMock()
            r.json.return_value = {"error": {"code": "ok"}, "data": {"status": s}}
            r.raise_for_status = MagicMock()
            responses.append(r)

        session = MagicMock()
        session.post.side_effect = responses
        client._session = session
        return client

    def test_complete_en_primer_intento(self):
        client = self._client_with(["PUBLISH_COMPLETE"])
        data = client.check_publish_status("pub1", max_polls=3, interval=0)
        assert data["status"] == "PUBLISH_COMPLETE"

    def test_pending_luego_complete(self):
        client = self._client_with(["PROCESSING_UPLOAD", "PUBLISH_COMPLETE"])
        data = client.check_publish_status("pub1", max_polls=3, interval=0)
        assert data["status"] == "PUBLISH_COMPLETE"

    def test_failed_lanza_con_fail_reason(self):
        r = MagicMock()
        r.json.return_value = {
            "error": {"code": "ok"},
            "data": {"status": "FAILED", "fail_reason": "duration_check_failed"},
        }
        r.raise_for_status = MagicMock()

        client = TikTokClient.__new__(TikTokClient)
        client._settings = MagicMock()
        client._settings.TIKTOK_MAX_VIDEO_DURATION_SEC = 600
        client._auth = MagicMock()
        client._auth.get_access_token.return_value = "tok"
        client._creator_info = None
        client._session = MagicMock()
        client._session.post.return_value = r

        with pytest.raises(TikTokAPIError) as exc:
            client.check_publish_status("pub1", max_polls=1, interval=0)
        assert "duration_check_failed" in str(exc.value)

    def test_no_reintentable_es_auth_removed(self):
        r = MagicMock()
        r.json.return_value = {
            "error": {"code": "ok"},
            "data": {"status": "FAILED", "fail_reason": "auth_removed"},
        }
        r.raise_for_status = MagicMock()

        client = TikTokClient.__new__(TikTokClient)
        client._settings = MagicMock()
        client._settings.TIKTOK_MAX_VIDEO_DURATION_SEC = 600
        client._auth = MagicMock()
        client._auth.get_access_token.return_value = "tok"
        client._creator_info = None
        client._session = MagicMock()
        client._session.post.return_value = r

        with pytest.raises(TikTokAPIError) as exc:
            client.check_publish_status("pub1", max_polls=1, interval=0)
        assert exc.value.retryable is False

    def test_timeout_es_reintentable(self):
        client = self._client_with(["PROCESSING_UPLOAD"] * 2)
        with pytest.raises(TikTokAPIError) as exc:
            client.check_publish_status("pub1", max_polls=2, interval=0)
        assert exc.value.code == "TIMEOUT"
        assert exc.value.retryable is True


class TestVideoValidation:
    def test_archivo_inexistente(self, tmp_path):
        pub = _publisher()
        err = pub._validate_video(tmp_path / "no.mp4")
        assert err is not None

    def test_llama_a_validate_externo(self, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"x" * 100)
        pub = _publisher()
        with patch("app.tiktok.publisher.validate", return_value=None) as mock_validate:
            assert pub._validate_video(v) is None
            mock_validate.assert_called_once()

    def test_propaga_error_de_validate(self, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"x" * 100)
        pub = _publisher()
        with patch("app.tiktok.publisher.validate", return_value="Duracion excedida"):
            err = pub._validate_video(v)
            assert err == "Duracion excedida"

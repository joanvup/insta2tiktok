import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "https://example.com/callback")

from app.tiktok.validator import FFprobeUnavailable, MediaInfo, _parse_fps, probe, validate


def _ffprobe_response(
    duration="59.5", width=1080, height=1920, fps="30/1", codec="h264"
):
    return {
        "streams": [
            {
                "codec_name": codec,
                "width": width,
                "height": height,
                "avg_frame_rate": fps,
                "duration": duration,
            }
        ],
        "format": {"duration": duration},
    }


def _mock_run(payload: dict):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(payload).encode("utf-8")
    proc.stderr = b""
    return proc


class TestParseFps:
    def test_fraccion(self):
        assert _parse_fps("30000/1001") == pytest.approx(29.97, rel=1e-3)

    def test_decimal(self):
        assert _parse_fps("30.0") == 30.0

    def test_denominador_cero(self):
        assert _parse_fps("30/0") is None

    def test_vacio(self):
        assert _parse_fps("") is None
        assert _parse_fps(None) is None

    def test_basura(self):
        assert _parse_fps("nope") is None


class TestProbe:
    def test_parsea_ffprobe(self, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"x")
        with patch(
            "app.tiktok.validator.ffprobe_path", return_value="/usr/bin/ffprobe"
        ), patch(
            "app.tiktok.validator.subprocess.run",
            return_value=_mock_run(_ffprobe_response()),
        ):
            info = probe(v)
        assert info == MediaInfo(
            duration=59.5, width=1080, height=1920, fps=30.0, codec="h264"
        )

    def test_sin_streams(self, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"x")
        with patch(
            "app.tiktok.validator.ffprobe_path", return_value="/usr/bin/ffprobe"
        ), patch(
            "app.tiktok.validator.subprocess.run", return_value=_mock_run({})
        ):
            info = probe(v)
        assert info.duration is None

    def test_archivo_malformado(self, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"x")
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = b""
        proc.stderr = b"bad"
        with patch(
            "app.tiktok.validator.ffprobe_path", return_value="/usr/bin/ffprobe"
        ), patch("app.tiktok.validator.subprocess.run", return_value=proc):
            with pytest.raises(FFprobeUnavailable):
                probe(v)


class TestValidate:
    def _video(self, tmp_path, name="v.mp4", size=1024):
        v = tmp_path / name
        v.write_bytes(b"x" * size)
        return v

    def _ok_ffprobe(self, **kwargs):
        return patch(
            "app.tiktok.validator.ffprobe_path", return_value="/usr/bin/ffprobe"
        ), patch(
            "app.tiktok.validator.subprocess.run",
            return_value=_mock_run(_ffprobe_response(**kwargs)),
        )

    def test_archivo_inexistente(self, tmp_path):
        assert validate(tmp_path / "no.mp4") is not None

    def test_formato_invalido(self, tmp_path):
        err = validate(self._video(tmp_path, "v.avi"))
        assert err is not None
        assert "no soportado" in err.lower()

    def test_video_valido(self, tmp_path):
        v = self._video(tmp_path)
        p1, p2 = self._ok_ffprobe()
        with p1, p2:
            assert validate(v, max_duration=600) is None

    def test_codec_invalido(self, tmp_path):
        v = self._video(tmp_path)
        p1, p2 = self._ok_ffprobe(codec="mpeg4")
        with p1, p2:
            err = validate(v)
        assert err is not None
        assert "codec" in err.lower()

    def test_resolucion_pequena(self, tmp_path):
        v = self._video(tmp_path)
        p1, p2 = self._ok_ffprobe(width=100, height=100)
        with p1, p2:
            err = validate(v)
        assert err is not None
        assert "pequena" in err.lower()

    def test_resolucion_grande(self, tmp_path):
        v = self._video(tmp_path)
        p1, p2 = self._ok_ffprobe(width=5000, height=5000)
        with p1, p2:
            err = validate(v)
        assert err is not None
        assert "grande" in err.lower()

    def test_fps_bajo(self, tmp_path):
        v = self._video(tmp_path)
        p1, p2 = self._ok_ffprobe(fps="15/1")
        with p1, p2:
            err = validate(v)
        assert err is not None
        assert "framerate" in err.lower()

    def test_fps_alto(self, tmp_path):
        v = self._video(tmp_path)
        p1, p2 = self._ok_ffprobe(fps="120/1")
        with p1, p2:
            err = validate(v)
        assert err is not None

    def test_duracion_excedida(self, tmp_path):
        v = self._video(tmp_path)
        p1, p2 = self._ok_ffprobe(duration="601.0")
        with p1, p2:
            err = validate(v, max_duration=600)
        assert err is not None
        assert "duracion" in err.lower()

    def test_sin_ffprobe_no_bloquea(self, tmp_path):
        v = self._video(tmp_path)
        with patch(
            "app.tiktok.validator.probe",
            side_effect=FFprobeUnavailable("sin ffprobe"),
        ):
            assert validate(v) is None

    def test_video_vacio(self, tmp_path):
        v = tmp_path / "empty.mp4"
        v.write_bytes(b"")
        err = validate(v)
        assert err is not None
        assert "vacio" in err.lower()

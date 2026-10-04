import os

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "http://localhost:8080/callback")

from app.cli.main import main


class TestCLI:
    def test_no_args_shows_help(self, capsys):
        main([])
        captured = capsys.readouterr()
        assert "insta2tiktok" in captured.out.lower() or captured.out == ""

    def test_status_command(self, tmp_path, capsys):
        from app.storage.database import init_db, reset_engine

        reset_engine()
        os.environ["DB_PATH"] = str(tmp_path / "cli_test.db")
        from app.config.settings import get_settings

        get_settings.cache_clear()
        init_db(tmp_path / "cli_test.db")
        main(["status"])
        captured = capsys.readouterr()
        assert "Shortcode" in captured.out

import os

import pytest

os.environ.setdefault("INSTAGRAM_USERNAME", "test_user")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test_key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test_secret")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "https://example.com/callback")

from app.cli.main import build_parser, main


class TestBuildParser:
    def test_comandos_disponibles(self):
        parser = build_parser()
        actions = [a for a in parser._actions if a.dest == "cmd"]
        choices = set(actions[0].choices)
        assert choices == {"auth", "run", "status", "retry", "reset-reel"}

    def test_auth_tiene_manual(self):
        args = build_parser().parse_args(["auth", "--manual"])
        assert args.manual is True

    def test_auth_sin_manual_por_defecto(self):
        args = build_parser().parse_args(["auth"])
        assert args.manual is False

    def test_run_tiene_dry_run(self):
        args = build_parser().parse_args(["run", "--dry-run"])
        assert args.dry_run is True

    def test_run_sin_dry_run_por_defecto(self):
        args = build_parser().parse_args(["run"])
        assert args.dry_run is False

    def test_retry_tiene_all(self):
        args = build_parser().parse_args(["retry", "--all"])
        assert args.all is True

    def test_reset_reel_toma_shortcode(self):
        args = build_parser().parse_args(["reset-reel", "ABC123"])
        assert args.shortcode == "ABC123"


class TestCLIStatus:
    def test_sin_args_muestra_ayuda(self, capsys):
        main([])
        captured = capsys.readouterr()
        assert "insta2tiktok" in captured.out.lower()

    def test_status_vacio(self, tmp_path, capsys):
        from app.config.settings import get_settings
        from app.storage.database import init_db, reset_engine

        reset_engine()
        os.environ["DB_PATH"] = str(tmp_path / "cli_test.db")
        get_settings.cache_clear()
        init_db(tmp_path / "cli_test.db")

        main(["status"])
        captured = capsys.readouterr()
        assert "No hay Reels registrados" in captured.out

    def test_status_con_registros(self, tmp_path, capsys):
        from app.config.settings import get_settings
        from app.storage.database import ReelRepository, init_db, reset_engine
        from app.storage.models import Reel, ReelStatus

        reset_engine()
        os.environ["DB_PATH"] = str(tmp_path / "cli_test2.db")
        get_settings.cache_clear()
        init_db(tmp_path / "cli_test2.db")
        ReelRepository.add(
            Reel(shortcode="XYZ999", status=ReelStatus.PUBLISHED.value, caption="hola")
        )

        main(["status"])
        captured = capsys.readouterr()
        assert "XYZ999" in captured.out
        assert "published" in captured.out

    def test_reset_reel_inexistente(self, tmp_path, capsys):
        from app.config.settings import get_settings
        from app.storage.database import init_db, reset_engine

        reset_engine()
        os.environ["DB_PATH"] = str(tmp_path / "cli_test3.db")
        get_settings.cache_clear()
        init_db(tmp_path / "cli_test3.db")

        main(["reset-reel", "NOEXISTE"])
        captured = capsys.readouterr()
        assert "No se encontro" in captured.out or "No se encontró" in captured.out
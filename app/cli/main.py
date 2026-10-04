from __future__ import annotations

import argparse
from typing import Optional

from ..config.settings import get_settings, setup_logging
from ..scheduler.jobs import Orchestrator, run_cycle
from ..storage.database import ReelRepository, init_db
from ..tiktok.auth import TikTokAuth


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insta2tiktok",
        description="Automatización de Reels de Instagram a TikTok",
    )
    sub = parser.add_subparsers(dest="cmd")

    auth = sub.add_parser("auth", help="Autenticar con TikTok")
    auth.add_argument(
        "--manual",
        action="store_true",
        help="Pegar el código OAuth a mano (servidores sin navegador)",
    )

    run = sub.add_parser("run", help="Ejecutar un ciclo completo")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar qué se haría sin descargar ni publicar",
    )

    sub.add_parser("status", help="Ver los últimos Reels registrados")

    retry = sub.add_parser("retry", help="Reintentar Reels fallidos")
    retry.add_argument(
        "--all",
        action="store_true",
        help="Ignorar el límite de reintentos configurado",
    )

    reset = sub.add_parser("reset-reel", help="Resetear el estado de un Reel")
    reset.add_argument("shortcode", help="Shortcode de Instagram")

    return parser


def main(args: Optional[list[str]] = None) -> None:
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    settings = get_settings()
    setup_logging(settings)
    init_db()

    if parsed_args.cmd == "auth":
        TikTokAuth(settings).authorize(manual=parsed_args.manual)
        print("Token guardado correctamente.")
        return

    if parsed_args.cmd == "run":
        run_cycle(dry_run=parsed_args.dry_run)
        return

    if parsed_args.cmd == "status":
        reels = ReelRepository.get_recent(10)
        if not reels:
            print("No hay Reels registrados todavía.")
            return
        print(f"{'SHORTCODE':<16}{'ESTADO':<20}{'PUBLICADO':<22}{'ERROR'}")
        for r in reels:
            published = r.published_at.strftime("%Y-%m-%d %H:%M") if r.published_at else "-"
            print(f"{r.shortcode:<16}{r.status:<20}{published:<22}{r.fail_reason or ''}")
        return

    if parsed_args.cmd == "retry":
        Orchestrator(settings).retry_failed(force_all=parsed_args.all)
        return

    if parsed_args.cmd == "reset-reel":
        reel = ReelRepository.reset_reel(parsed_args.shortcode)
        if reel is None:
            print(f"No se encontró ningún Reel con shortcode '{parsed_args.shortcode}'.")
            return
        print(f"Reel {reel.shortcode} reseteado a DOWNLOADED.")

    if not parsed_args.cmd:
        parser.print_help()


if __name__ == "__main__":
    main()
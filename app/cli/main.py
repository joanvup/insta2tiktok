from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from ..config.settings import Settings, get_settings, setup_logging
from ..scheduler.jobs import Orchestrator, run_cycle
from ..storage.database import ReelRepository, init_db
from ..tiktok.auth import TikTokAuth


def main(args: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="insta2tiktok", description="Automatización de Reels a TikTok")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("auth", help="Obtain TikTok OAuth token")

    run = sub.add_parser("run", help="Execute once")
    run.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="Show recent status")

    retry = sub.add_parser("retry", help="Retry failed reels")
    retry.add_argument("--all", action="store_true")

    reset = sub.add_parser("reset-reel", help="Reset a specific reel")
    reset.add_argument("shortcode", help="Instagram shortcode")

    parsed_args = parser.parse_args(args)

    settings = get_settings()
    setup_logging(settings)
    init_db()

    if parsed_args.cmd == "auth":
        auth = TikTokAuth(settings)
        auth.authorize()
        print("Token stored successfully")
        return

    if parsed_args.cmd == "run":
        run_cycle(dry_run=parsed_args.dry_run)
        return

    if parsed_args.cmd == "status":
        reels = ReelRepository.get_recent(10)
        print(f"{'Shortcode':<15} {'Status':<15} {'Published'}")
        for r in reels:
            print(f"{r.shortcode:<15} {r.status:<15} {r.published_at}")
        return

    if parsed_args.cmd == "retry":
        orchestrator = Orchestrator(settings)
        orchestrator.retry_failed()
        return

    if parsed_args.cmd == "reset-reel":
        ReelRepository.reset_reel(parsed_args.shortcode)
        print(f"Reel {parsed_args.shortcode} reset to DOWNLOADED")
        return

    parser.print_help()


if __name__ == "__main__":
    main()

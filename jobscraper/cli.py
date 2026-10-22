"""Command line entry point: python -m jobscraper.cli <command>."""

from __future__ import annotations

import argparse
import logging

from jobscraper import db
from jobscraper.config import load_config
from jobscraper.keys import KeyStore
from jobscraper.models import SearchQuery
from jobscraper.profile import load_profile
from jobscraper.scrape import run_source
from jobscraper.sources import SCRAPERS, SourceUnavailable, create_source

log = logging.getLogger("jobscraper")


def cmd_status(args: argparse.Namespace) -> None:
    config = load_config()
    with db.open_db(config.paths.db) as conn:
        counts = db.count_by_status(conn)
    log.info("Database: %s", config.paths.db)
    log.info("Jobs by status: %s", {str(k): v for k, v in counts.items()} or "none yet")
    log.info("Enabled sources: %s", ", ".join(config.enabled_sources()) or "none")
    profile = load_profile(config.paths.profile)
    if profile is None:
        log.warning("No profile at %s - copy profile.example.yaml there", config.paths.profile)
    else:
        log.info("Profile: %s", profile.contact.full_name)


def cmd_scrape(args: argparse.Namespace) -> None:
    config = load_config()
    keys = KeyStore()  # CLI: keys come from .env / the environment
    source_names = [args.source] if args.source else config.enabled_sources()
    keywords = [args.query] if args.query else config.search.queries
    pages = args.pages or config.scraping.max_pages_per_query
    queries = [SearchQuery(keywords=k, city=args.city, max_pages=pages) for k in keywords]

    with db.open_db(config.paths.db) as conn:
        for name in source_names:
            try:
                source = create_source(name, config, keys)
            except SourceUnavailable as exc:
                log.error("%s: %s", name, exc)
                continue
            # Direct scrapers also load the full ads (free); LinkedIn details cost a credit each
            describe = config.scraping.auto_details and not args.no_details and name in SCRAPERS
            for query in queries:
                outcome = run_source(
                    conn, source, query, details=source if describe else None, max_details=config.scraping.max_auto_details
                )
                if outcome.error:
                    log.error("%s %r: %s", name, query.keywords, outcome.error)


def cmd_demo(args: argparse.Namespace) -> None:
    from jobscraper.demo import seed_demo

    config = load_config()
    result = seed_demo(config)
    log.info("Demo jobs: %d (%d new) in %s", result.jobs, result.new_jobs, config.paths.db)
    if result.profile_created:
        log.info("Example profile copied to %s", config.paths.profile)
    else:
        log.info("Kept the existing profile at %s", config.paths.profile)
    log.info("Demo CVs added: %d", result.cvs)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too noisy
    parser = argparse.ArgumentParser(prog="jobscraper")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="create the database if needed and show a summary").set_defaults(
        func=cmd_status
    )
    scrape = commands.add_parser("scrape", help="fetch jobs from the enabled sources")
    scrape.add_argument("--source", help="only this source (default: all enabled in config.yaml)")
    scrape.add_argument("--query", help="search keywords (default: search.queries from config.yaml)")
    scrape.add_argument("--city")
    scrape.add_argument("--pages", type=int, help="result pages per query (default: scraping.max_pages_per_query)")
    scrape.add_argument("--no-details", action="store_true", help="do not load the full ads (scraping.auto_details)")
    scrape.set_defaults(func=cmd_scrape)
    commands.add_parser(
        "demo", help="add demo jobs, the example profile and two CVs (no API keys, no network)"
    ).set_defaults(func=cmd_demo)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

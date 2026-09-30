from __future__ import annotations

import argparse
import sqlite3
import sys
from urllib.parse import urljoin

try:
    from media_db import connect, seed_catalog
    from sync_media import BASE_URL, MovySync
except ModuleNotFoundError as exc:
    missing = exc.name or "a required package"
    print(f"Missing {missing}.")
    print("Install the project requirements first:")
    print(r"  py -m pip install -r requirements.txt")
    raise SystemExit(1)


def export_ids(connection: sqlite3.Connection) -> tuple[int, int]:
    movie_ids = [
        row[0]
        for row in connection.execute(
            "SELECT media_id FROM media WHERE media_type='movie' ORDER BY CAST(media_id AS INTEGER)"
        )
    ]
    show_ids = [
        row[0]
        for row in connection.execute(
            "SELECT media_id FROM media WHERE media_type='tv' ORDER BY CAST(media_id AS INTEGER)"
        )
    ]
    from media_db import ROOT

    (ROOT / "movie-ids.txt").write_text("\n".join(movie_ids) + "\n", encoding="utf-8")
    (ROOT / "show-ids.txt").write_text("\n".join(show_ids) + "\n", encoding="utf-8")
    return len(movie_ids), len(show_ids)


def stats(connection: sqlite3.Connection) -> dict[str, int]:
    def count(sql: str) -> int:
        return int(connection.execute(sql).fetchone()[0])

    return {
        "movies": count("SELECT COUNT(*) FROM media WHERE media_type='movie'"),
        "shows": count("SELECT COUNT(*) FROM media WHERE media_type='tv'"),
        "people": count("SELECT COUNT(*) FROM people"),
        "seasons": count("SELECT COUNT(*) FROM seasons"),
        "episodes": count("SELECT COUNT(*) FROM episodes"),
        "image_records": count("SELECT COUNT(*) FROM images"),
        "cached_images": count("SELECT COUNT(*) FROM images WHERE local_path IS NOT NULL"),
        "pending_pages": count("SELECT COUNT(*) FROM crawl_queue WHERE status='pending'"),
        "failed_pages": count("SELECT COUNT(*) FROM crawl_queue WHERE status='error'"),
    }


def print_stats(connection: sqlite3.Connection) -> None:
    values = stats(connection)
    print("\nDatabase status")
    print("-" * 52)
    for name, value in values.items():
        print(f"{name.replace('_', ' ').title():24} {value:,}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Incrementally build the local movie/TV database and asset cache."
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=100,
        help="Maximum movie/show/episode pages to fetch this run (default: 100)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between web requests in seconds (default: 1.0)",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--type", choices=("all", "movie", "tv"), default="all",
        help="Scrape both types, movies only, or TV shows only (default: all)",
    )
    parser.add_argument("--year-from", type=int, help="Only add titles released in this year or later")
    parser.add_argument("--year-to", type=int, help="Only add titles released in this year or earlier")
    parser.add_argument("--rating-min", type=float, help="Minimum IMDb rating, for example 7.0")
    parser.add_argument("--rating-max", type=float, help="Maximum IMDb rating")
    parser.add_argument(
        "--genre", action="append", default=[],
        help="Genre to include; repeat it or use commas for alternatives (for example: Comedy,Drama)",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Store image URLs but do not download image files",
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Continue the saved queue without checking browse/sitemap pages",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Move failed pages back to the pending queue before starting",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep processing batches until the pending queue is empty",
    )
    args = parser.parse_args()
    if args.year_from and args.year_to and args.year_from > args.year_to:
        parser.error("--year-from cannot be greater than --year-to")
    if args.rating_min is not None and not 0 <= args.rating_min <= 10:
        parser.error("--rating-min must be between 0 and 10")
    if args.rating_max is not None and not 0 <= args.rating_max <= 10:
        parser.error("--rating-max must be between 0 and 10")
    if args.rating_min is not None and args.rating_max is not None and args.rating_min > args.rating_max:
        parser.error("--rating-min cannot be greater than --rating-max")
    genres = [item.strip() for value in args.genre for item in value.split(",") if item.strip()]

    connection = connect()
    seeded = seed_catalog(connection)
    for seed in ("/movie/969681", "/tv/97546"):
        connection.execute(
            "INSERT OR IGNORE INTO crawl_queue(url,discovered_from) VALUES(?,?)",
            (urljoin(BASE_URL, seed), "built-in seed"),
        )
    if args.retry_errors:
        connection.execute(
            "UPDATE crawl_queue SET status='pending',last_error=NULL WHERE status='error'"
        )
    connection.commit()

    print(f"Loaded {seeded:,} IDs from the bundled catalogue.")
    sync = MovySync(
        args.delay, not args.metadata_only, args.timeout,
        media_type=args.type,
        year_from=args.year_from,
        year_to=args.year_to,
        rating_min=args.rating_min,
        rating_max=args.rating_max,
        genres=genres,
    )
    migrated = sync.migrate_cached_images()
    if migrated:
        print(f"Moved {migrated:,} cached images into the asset-specific folders.")

    try:
        if not args.skip_discovery:
            print("Checking public browse pages and sitemaps for IDs...")
            sync.discover_seed_pages()

        while True:
            before = sync.pending_count()
            if before == 0:
                print("No pending pages remain.")
                break
            batch_size = min(args.pages, before)
            print(f"Processing up to {batch_size:,} of {before:,} pending pages...")
            sync.run(batch_size)
            after = sync.pending_count()
            if not args.continuous or after == 0 or after >= before:
                break
    except KeyboardInterrupt:
        print("\nStopped. Progress has been saved and the next run will resume.")
    finally:
        movie_count, show_count = export_ids(sync.db)
        sync.db.commit()
        print_stats(sync.db)
        print(f"\nExported {movie_count:,} movie IDs to movie-ids.txt")
        print(f"Exported {show_count:,} show IDs to show-ids.txt")

    return 0


if __name__ == "__main__":
    sys.exit(main())

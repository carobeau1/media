from __future__ import annotations

from media_db import ROOT, connect


def main() -> None:
    connection = connect()
    movie_ids = [row[0] for row in connection.execute("SELECT media_id FROM media WHERE media_type='movie' ORDER BY CAST(media_id AS INTEGER)")]
    show_ids = [row[0] for row in connection.execute("SELECT media_id FROM media WHERE media_type='tv' ORDER BY CAST(media_id AS INTEGER)")]
    (ROOT / "movie-ids.txt").write_text("\n".join(movie_ids) + "\n", encoding="utf-8")
    (ROOT / "show-ids.txt").write_text("\n".join(show_ids) + "\n", encoding="utf-8")
    print(f"Exported {len(movie_ids)} movie IDs and {len(show_ids)} show IDs")


if __name__ == "__main__":
    main()

"""Delete a title and its exclusively owned metadata and cached artwork."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from media_db import ROOT, connect
from media_db import ROOT as MEDIA_ROOT

CACHE_ROOT = MEDIA_ROOT / "cache" / "images"


def delete_media(media_type: str, media_id: str) -> dict:
    if media_type not in {"movie", "tv"} or not str(media_id).isdigit():
        raise ValueError("Expected movie or tv and a numeric ID")
    media_id = str(media_id)
    db = connect()
    row = db.execute("SELECT title FROM media WHERE media_type=? AND media_id=?", (media_type, media_id)).fetchone()
    if row is None:
        raise LookupError(f"{media_type}/{media_id} is not in the database")
    people = [r[0] for r in db.execute("SELECT person_key FROM credits WHERE media_type=? AND media_id=?", (media_type, media_id))]
    orphan_people = [key for key in people if db.execute(
        "SELECT COUNT(*) FROM credits WHERE person_key=? AND NOT (media_type=? AND media_id=?)",
        (key, media_type, media_id),
    ).fetchone()[0] == 0]
    episode_prefix = f"{media_id}:"
    rows = db.execute("SELECT image_id,local_path,owner_type,owner_key FROM images").fetchall()
    owned = [r for r in rows if (r['owner_type'] == media_type and r['owner_key'] == media_id)
             or (media_type == 'tv' and r['owner_type'] == 'episode' and r['owner_key'].startswith(episode_prefix))
             or (r['owner_type'] == 'person' and r['owner_key'] in orphan_people)]
    paths = {r['local_path'] for r in owned if r['local_path']}
    # Files shared by another image row must remain available.
    retained_paths = {r['local_path'] for r in rows if r['image_id'] not in {o['image_id'] for o in owned}}
    db.execute("BEGIN")
    try:
        db.execute("DELETE FROM media WHERE media_type=? AND media_id=?", (media_type, media_id))
        db.execute("DELETE FROM recommendations WHERE (media_type=? AND media_id=?) OR (related_type=? AND related_id=?)",
                   (media_type, media_id, media_type, media_id))
        db.execute("DELETE FROM images WHERE owner_type=? AND owner_key=?", (media_type, media_id))
        if media_type == 'tv':
            db.execute("DELETE FROM images WHERE owner_type='episode' AND owner_key LIKE ?", (episode_prefix + '%',))
            db.execute("DELETE FROM seasons WHERE show_id=?", (media_id,))
            db.execute("DELETE FROM crawl_queue WHERE url LIKE ?", (f'%/tv/{media_id}/%',))
        for key in orphan_people:
            db.execute("DELETE FROM images WHERE owner_type='person' AND owner_key=?", (key,))
            db.execute("DELETE FROM people WHERE person_key=?", (key,))
        db.execute("DELETE FROM genres WHERE NOT EXISTS (SELECT 1 FROM media_genres WHERE genre_id=genres.genre_id)")
        db.execute("DELETE FROM crawl_queue WHERE url LIKE ?", (f'%/{media_type}/{media_id}',))
        db.commit()
    except Exception:
        db.rollback()
        raise
    removed_files = 0
    for relative in paths - retained_paths:
        path = CACHE_ROOT / relative
        if path.is_file() and path.resolve().is_relative_to(CACHE_ROOT.resolve()):
            path.unlink()
            removed_files += 1
            parent = path.parent
            while parent != CACHE_ROOT and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    for name, kind in (("movie-ids.txt", "movie"), ("show-ids.txt", "tv")):
        ids = [r[0] for r in db.execute("SELECT media_id FROM media WHERE media_type=? ORDER BY CAST(media_id AS INTEGER)", (kind,))]
        (ROOT / name).write_text("\n".join(ids) + "\n", encoding="utf-8")
    # Catalog and legacy detail files must not resurrect the deleted title.
    catalog = ROOT / "catalog.json"
    if catalog.exists():
        items = json.loads(catalog.read_text(encoding="utf-8"))
        kept = [item for item in items if not (item.get("type") == media_type and
                item.get("url", "").rstrip("/") == f"/{media_type}/{media_id}")]
        if len(kept) != len(items):
            catalog.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    legacy = ROOT / ("movie_details.json" if media_type == "movie" else "show_details.json")
    if legacy.exists():
        details = json.loads(legacy.read_text(encoding="utf-8"))
        if media_id in details:
            del details[media_id]
            legacy.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    db.close()
    return {"type": media_type, "id": media_id, "title": row['title'], "cached_files_removed": removed_files}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Permanently delete a movie or TV show and its associated files")
    parser.add_argument("type", choices=["movie", "tv"])
    parser.add_argument("id", help="Numeric title ID")
    args = parser.parse_args()
    print(json.dumps(delete_media(args.type, args.id), indent=2))

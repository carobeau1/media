from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "media.db"


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS media (
    media_type TEXT NOT NULL CHECK(media_type IN ('movie','tv')),
    media_id TEXT NOT NULL,
    title TEXT NOT NULL,
    original_title TEXT,
    description TEXT,
    year INTEGER,
    release_date TEXT,
    rating REAL,
    runtime_minutes INTEGER,
    certification TEXT,
    seasons_count INTEGER,
    source_url TEXT NOT NULL,
    raw_json TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(media_type, media_id)
);

CREATE TABLE IF NOT EXISTS genres (
    genre_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS media_genres (
    media_type TEXT NOT NULL,
    media_id TEXT NOT NULL,
    genre_id INTEGER NOT NULL,
    PRIMARY KEY(media_type, media_id, genre_id),
    FOREIGN KEY(media_type, media_id) REFERENCES media(media_type, media_id) ON DELETE CASCADE,
    FOREIGN KEY(genre_id) REFERENCES genres(genre_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_countries (
    media_type TEXT NOT NULL,
    media_id TEXT NOT NULL,
    country TEXT NOT NULL,
    PRIMARY KEY(media_type, media_id, country),
    FOREIGN KEY(media_type, media_id) REFERENCES media(media_type, media_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metadata_refresh (
    media_type TEXT NOT NULL,
    media_id TEXT NOT NULL,
    last_attempt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY(media_type, media_id),
    FOREIGN KEY(media_type, media_id) REFERENCES media(media_type, media_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hidden_media (
    media_type TEXT NOT NULL CHECK(media_type IN ('movie','tv')),
    media_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    hidden_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(media_type, media_id)
);

CREATE TABLE IF NOT EXISTS people (
    person_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_url TEXT
);

CREATE TABLE IF NOT EXISTS credits (
    media_type TEXT NOT NULL,
    media_id TEXT NOT NULL,
    person_key TEXT NOT NULL,
    character_name TEXT,
    credit_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(media_type, media_id, person_key, character_name),
    FOREIGN KEY(media_type, media_id) REFERENCES media(media_type, media_id) ON DELETE CASCADE,
    FOREIGN KEY(person_key) REFERENCES people(person_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS seasons (
    show_id TEXT NOT NULL,
    season_number INTEGER NOT NULL,
    title TEXT,
    episode_count INTEGER,
    PRIMARY KEY(show_id, season_number)
);

CREATE TABLE IF NOT EXISTS episodes (
    show_id TEXT NOT NULL,
    season_number INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    title TEXT,
    description TEXT,
    air_date TEXT,
    runtime_minutes INTEGER,
    rating REAL,
    source_url TEXT,
    raw_json TEXT,
    PRIMARY KEY(show_id, season_number, episode_number),
    FOREIGN KEY(show_id, season_number) REFERENCES seasons(show_id, season_number) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS images (
    image_id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type TEXT NOT NULL CHECK(owner_type IN ('movie','tv','episode','person')),
    owner_key TEXT NOT NULL,
    image_type TEXT NOT NULL CHECK(image_type IN ('poster','backdrop','logo','episode','person','other')),
    source_url TEXT NOT NULL,
    local_path TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    sha256 TEXT,
    fetched_at TEXT,
    UNIQUE(owner_type, owner_key, image_type, source_url)
);

CREATE TABLE IF NOT EXISTS recommendations (
    media_type TEXT NOT NULL,
    media_id TEXT NOT NULL,
    related_type TEXT NOT NULL,
    related_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(media_type, media_id, related_type, related_id)
);

CREATE TABLE IF NOT EXISTS crawl_queue (
    url TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','error')),
    attempts INTEGER NOT NULL DEFAULT 0,
    discovered_from TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_media_title ON media(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_images_owner ON images(owner_type, owner_key, image_type);
CREATE INDEX IF NOT EXISTS idx_queue_status ON crawl_queue(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_media_countries_country ON media_countries(country, media_type);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def seed_catalog(connection: sqlite3.Connection, catalog_path: Path | None = None) -> int:
    catalog_path = catalog_path or ROOT / "catalog.json"
    if not catalog_path.exists():
        return 0
    records = json.loads(catalog_path.read_text(encoding="utf-8"))
    count = 0
    for item in records:
        url = item.get("url", "")
        parts = url.strip("/").split("/")
        if len(parts) != 2 or parts[0] not in {"movie", "tv"} or not parts[1].isdigit():
            continue
        media_type, media_id = parts
        connection.execute(
            """INSERT INTO media(media_type,media_id,title,year,rating,source_url,raw_json)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(media_type,media_id) DO UPDATE SET
                 title=excluded.title,
                 year=COALESCE(excluded.year,media.year),
                 rating=COALESCE(excluded.rating,media.rating),
                 source_url=excluded.source_url""",
            (
                media_type,
                media_id,
                item.get("title") or f"{media_type} {media_id}",
                int(item["year"]) if str(item.get("year", "")).isdigit() else None,
                float(item["rating"]) if str(item.get("rating", "")).replace(".", "", 1).isdigit() else None,
                f"https://www.movy.sx/{media_type}/{media_id}",
                json.dumps(item, ensure_ascii=False),
            ),
        )
        connection.execute(
            "INSERT OR IGNORE INTO crawl_queue(url,discovered_from) VALUES(?,?)",
            (f"https://www.movy.sx/{media_type}/{media_id}", "catalog.json"),
        )
        count += 1
    connection.commit()
    return count


def media_for_app(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT m.*,
                  (SELECT local_path FROM images i
                   WHERE i.owner_type=m.media_type AND i.owner_key=m.media_id
                     AND i.image_type='poster' AND i.local_path IS NOT NULL
                   ORDER BY i.image_id LIMIT 1) AS poster_local,
                  (SELECT source_url FROM images i
                   WHERE i.owner_type=m.media_type AND i.owner_key=m.media_id
                     AND i.image_type='poster'
                   ORDER BY i.image_id LIMIT 1) AS poster_remote
           FROM media m WHERE NOT EXISTS (
               SELECT 1 FROM hidden_media h WHERE h.media_type=m.media_type AND h.media_id=m.media_id
           ) ORDER BY m.title COLLATE NOCASE"""
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        poster = f"/media-cache/{item['poster_local']}" if item.get("poster_local") else item.get("poster_remote", "")
        result.append({
            "id": item["media_id"],
            "title": item["title"],
            "type": item["media_type"],
            "type_label": "TV" if item["media_type"] == "tv" else "Movie",
            "year": str(item["year"] or ""),
            "rating": f"{item['rating']:.1f}" if item["rating"] is not None else "",
            "poster": poster or "",
            "url": f"/{item['media_type']}/{item['media_id']}",
        })
    return result

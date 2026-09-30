from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from media_db import ROOT, connect, seed_catalog
from request_pacing import outgoing_request, is_interactive


BASE_URL = "https://www.movy.sx"
CACHE_ROOT = ROOT / "cache" / "images"
MIN_BACKGROUND_IMAGE_FREE_BYTES = 2 * 1024 ** 3


class BackgroundImagePaused(Exception):
    """Background image downloads wait until the app drive has at least 2 GiB free."""


def check_background_image_space(next_chunk_bytes=0):
    if not is_interactive() and shutil.disk_usage(ROOT).free - next_chunk_bytes < MIN_BACKGROUND_IMAGE_FREE_BYTES:
        raise BackgroundImagePaused('Background image downloads paused: app drive has less than 2 GB free.')


DETAIL_RE = re.compile(r"^/(movie|tv)/(\d+)(?:$|[/?#])")
EPISODE_RE = re.compile(r"^/tv/(\d+)/(\d+)/(\d+)(?:$|[/?#])")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
RUNTIME_RE = re.compile(r"(?:(\d+)h\s*)?(\d+)m")


def episode_runtime_minutes(value):
    if value in (None, '') or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if 0 < value < 1000 else None
    text = str(value).strip()
    if text.isdigit():
        return episode_runtime_minutes(int(text))
    match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?', text, re.I)
    if match and any(match.groups()):
        return episode_runtime_minutes(int(match.group(1) or 0) * 60 + int(match.group(2) or 0))
    match = RUNTIME_RE.fullmatch(text)
    if match:
        return episode_runtime_minutes(int(match.group(1) or 0) * 60 + int(match.group(2)))
    match = re.fullmatch(r'(\d{1,2}):(\d{2})(?::\d{2})?', text)
    if match:
        return episode_runtime_minutes(int(match.group(1)) * 60 + int(match.group(2)))
    return None


def episode_rating(value):
    try:
        result = float(value)
        return result if 0 <= result <= 10 else None
    except (TypeError, ValueError):
        return None


def safe_path_part(value: object) -> str:
    """Return a Windows-safe folder name for a database identifier."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value)).strip(" .")
    return cleaned or "unknown"


def image_cache_path(row, extension: str) -> Path:
    """Choose an asset-first cache path relative to cache/images."""
    image_type = row["image_type"] or "other"
    owner_type = safe_path_part(row["owner_type"])
    owner_key = str(row["owner_key"])
    digest = hashlib.sha256(row["source_url"].encode("utf-8")).hexdigest()[:16]

    if image_type == "person":
        folder = Path("people") / safe_path_part(owner_key)
    elif image_type == "episode":
        parts = owner_key.split(":")
        if len(parts) == 3:
            show_id, season, episode = parts
            folder = Path("episodes") / safe_path_part(show_id) / f"S{int(season):02d}E{int(episode):02d}"
        else:
            folder = Path("episodes") / safe_path_part(owner_key)
    else:
        category = {
            "poster": "covers",
            "backdrop": "backdrops",
            "logo": "logos",
        }.get(image_type, "other")
        folder = Path(category) / owner_type / safe_path_part(owner_key)

    return folder / f"{safe_path_part(image_type)}-{digest}{extension}"


def normal_image_url(url: str) -> str:
    if not url:
        return ""
    url = urljoin(BASE_URL, url)
    parsed = urlparse(url)
    if parsed.netloc == "wsrv.nl":
        target = parse_qs(parsed.query).get("url", [""])[0]
        if target:
            return unquote(target)
    return url


def best_src(tag) -> str:
    if not tag:
        return ""
    src = tag.get("src") or tag.get("data-src") or tag.get("data-savepage-src") or ""
    if not src and tag.get("srcset"):
        src = tag["srcset"].split(",")[-1].strip().split(" ")[0]
    return normal_image_url(src)


def json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_objects(child)


class MovySync:
    def __init__(
        self,
        delay: float,
        download_images: bool,
        timeout: int,
        media_type: str = "all",
        year_from: int | None = None,
        year_to: int | None = None,
        rating_min: float | None = None,
        rating_max: float | None = None,
        genres: list[str] | None = None,
    ):
        self.delay = delay
        self.download_images = download_images
        self.timeout = timeout
        self.media_type = media_type
        self.year_from = year_from
        self.year_to = year_to
        self.rating_min = rating_min
        self.rating_max = rating_max
        self.genres = {genre.strip().casefold() for genre in (genres or []) if genre.strip()}
        self.db = connect()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36",
            "Accept-Language": "en-AU,en;q=0.9",
        })
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    @property
    def has_content_filters(self) -> bool:
        return any((self.year_from, self.year_to, self.rating_min is not None,
                    self.rating_max is not None, self.genres))

    def type_allowed(self, media_type: str) -> bool:
        return self.media_type == "all" or media_type == self.media_type

    def values_allowed(self, year, rating, genres) -> bool:
        try:
            numeric_year = int(year) if year not in (None, "") else None
        except (TypeError, ValueError):
            numeric_year = None
        try:
            numeric_rating = float(rating) if rating not in (None, "") else None
        except (TypeError, ValueError):
            numeric_rating = None
        available_genres = {str(value).strip().casefold() for value in (genres or [])}
        if self.year_from is not None and (numeric_year is None or numeric_year < self.year_from):
            return False
        if self.year_to is not None and (numeric_year is None or numeric_year > self.year_to):
            return False
        if self.rating_min is not None and (numeric_rating is None or numeric_rating < self.rating_min):
            return False
        if self.rating_max is not None and (numeric_rating is None or numeric_rating > self.rating_max):
            return False
        return not self.genres or bool(self.genres & available_genres)

    def media_allowed(self, media_type: str, media_id: str) -> bool:
        if not self.type_allowed(media_type):
            return False
        row = self.db.execute(
            "SELECT year,rating FROM media WHERE media_type=? AND media_id=?",
            (media_type, media_id),
        ).fetchone()
        if not row:
            return not self.has_content_filters
        genres = [item[0] for item in self.db.execute(
            """SELECT g.name FROM genres g JOIN media_genres mg ON mg.genre_id=g.genre_id
               WHERE mg.media_type=? AND mg.media_id=?""",
            (media_type, media_id),
        )]
        return self.values_allowed(row["year"], row["rating"], genres)

    def image_allowed(self, row) -> bool:
        if row["owner_type"] in {"movie", "tv"}:
            return self.media_allowed(row["owner_type"], row["owner_key"])
        if row["owner_type"] == "episode":
            return self.media_allowed("tv", str(row["owner_key"]).split(":", 1)[0])
        if row["owner_type"] == "person":
            credits = self.db.execute(
                "SELECT media_type,media_id FROM credits WHERE person_key=?",
                (row["owner_key"],),
            ).fetchall()
            return any(self.media_allowed(item["media_type"], item["media_id"]) for item in credits)
        return False

    def pending_count(self) -> int:
        pattern = "%/movie/%" if self.media_type == "movie" else "%/tv/%"
        if self.media_type == "all":
            return int(self.db.execute(
                "SELECT COUNT(*) FROM crawl_queue WHERE status='pending'"
            ).fetchone()[0])
        return int(self.db.execute(
            "SELECT COUNT(*) FROM crawl_queue WHERE status='pending' AND url LIKE ?",
            (pattern,),
        ).fetchone()[0])

    def enqueue(self, url: str, parent: str = "manual") -> None:
        url = urljoin(BASE_URL, url).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.netloc not in {"movy.sx", "www.movy.sx"}:
            return
        if not (DETAIL_RE.match(parsed.path) or EPISODE_RE.match(parsed.path)):
            return
        detail = DETAIL_RE.match(parsed.path)
        episode = EPISODE_RE.match(parsed.path)
        candidate_type = detail.group(1) if detail else "tv"
        if not self.type_allowed(candidate_type) or (episode and self.media_type == "movie"):
            return
        if episode and self.db.execute(
            "SELECT 1 FROM hidden_media WHERE media_type='tv' AND media_id=?", (episode.group(1),)
        ).fetchone():
            return
        if detail:
            media_type, media_id = detail.groups()
            if self.db.execute(
                "SELECT 1 FROM hidden_media WHERE media_type=? AND media_id=?", (media_type, media_id)
            ).fetchone():
                return
        self.db.execute("INSERT OR IGNORE INTO crawl_queue(url,discovered_from) VALUES(?,?)", (url, parent))
        if detail:
            self.db.execute(
                """INSERT OR IGNORE INTO media(media_type,media_id,title,source_url,raw_json)
                   VALUES(?,?,?,?,?)""",
                (media_type, media_id, f"{media_type.title()} {media_id}", url, "{}"),
            )

    def discover_seed_pages(self) -> None:
        for path in ("/", "/browse/movie", "/browse/tv", "/browse/anime"):
            url = urljoin(BASE_URL, path)
            try:
                soup = BeautifulSoup(self.fetch(url), "html.parser")
                self.discover_links(soup, url)
                self.db.commit()
                print(f"discovered IDs from {url}")
            except Exception as exc:
                print(f"DISCOVERY ERROR {url}: {exc}")
            time.sleep(self.delay)
        for path in ("/sitemap.xml", "/sitemap-index.xml"):
            url = urljoin(BASE_URL, path)
            try:
                with outgoing_request(url):
                    response = self.session.get(url, timeout=self.timeout)
                if response.ok:
                    for location in re.findall(r"<loc>\s*([^<]+)\s*</loc>", response.text, re.I):
                        self.enqueue(location, url)
                    self.db.commit()
                    print(f"discovered IDs from {url}")
            except Exception as exc:
                print(f"SITEMAP ERROR {url}: {exc}")
            time.sleep(self.delay)

    def fetch(self, url: str) -> str:
        with outgoing_request(url):
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "text/html"):
                raise ValueError(f"Unexpected content type: {response.headers.get('content-type')}")
            return response.text

    def parse_json_data(self, soup: BeautifulSoup) -> list[dict]:
        objects: list[dict] = []
        for script in soup.find_all("script"):
            script_type = script.get("type", "")
            if script.get("id") != "__NEXT_DATA__" and "json" not in script_type:
                continue
            text = script.string or script.get_text() or ""
            try:
                value = json.loads(text)
            except (ValueError, TypeError):
                continue
            objects.extend(json_objects(value))
        return objects

    def next_page_props(self, soup: BeautifulSoup) -> dict:
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            return {}
        try:
            return json.loads(script.string or script.get_text())["props"]["pageProps"]
        except (ValueError, TypeError, KeyError):
            return {}

    def discover_links(self, soup: BeautifulSoup, parent: str) -> None:
        for anchor in soup.find_all("a", href=True):
            self.enqueue(anchor["href"], parent)
        for obj in self.parse_json_data(soup):
            media_id = obj.get("id")
            media_type = obj.get("mediaType") or obj.get("media_type")
            if str(media_id).isdigit() and media_type in {"movie", "tv"}:
                self.enqueue(f"/{media_type}/{media_id}", parent)
                release_date = obj.get("release_date") or obj.get("first_air_date") or ""
                year = int(release_date[:4]) if str(release_date)[:4].isdigit() else None
                self.db.execute(
                    """UPDATE media SET title=COALESCE(NULLIF(?,''),title),year=COALESCE(?,year),
                       rating=COALESCE(?,rating),raw_json=? WHERE media_type=? AND media_id=?""",
                    (obj.get("title") or obj.get("name") or "", year, obj.get("rating"),
                     json.dumps(obj, ensure_ascii=False), media_type, str(media_id)),
                )
                if not self.has_content_filters:
                    self.record_image(media_type, str(media_id), "poster", normal_image_url(obj.get("poster", "")))
                    self.record_image(media_type, str(media_id), "backdrop", normal_image_url(obj.get("image", "")))

    def classify_image(self, img, media_title: str, in_cast: bool, in_episode: bool) -> str:
        src = best_src(img).lower()
        alt = (img.get("alt") or "").strip()
        if in_episode:
            return "episode"
        if in_cast and alt and alt.casefold() != media_title.casefold():
            return "person"
        if src.endswith(".png") and alt.casefold() in {media_title.casefold(), ""}:
            return "logo"
        if "/original/" in src or "backdrop" in src:
            return "backdrop"
        if any(token in src for token in ("/w185/", "/w300/", "/w342/", "/w500/", "poster")):
            return "poster"
        return "other"

    def record_image(self, owner_type: str, owner_key: str, image_type: str, source_url: str) -> None:
        if not source_url or source_url.startswith("data:"):
            return
        self.db.execute(
            """INSERT OR IGNORE INTO images(owner_type,owner_key,image_type,source_url)
               VALUES(?,?,?,?)""",
            (owner_type, owner_key, image_type, source_url),
        )

    def cache_image(self, row) -> None:
        url = row["source_url"]
        check_background_image_space()
        with outgoing_request(url):
            # The request may have spent time waiting behind an interactive page.
            check_background_image_space()
            response = self.session.get(url, timeout=self.timeout, stream=True)
            try:
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";", 1)[0]
                extension = mimetypes.guess_extension(mime) or Path(urlparse(url).path).suffix or ".img"
                rel = image_cache_path(row, extension)
                target = CACHE_ROOT / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                hasher = hashlib.sha256()
                try:
                    with target.open("wb") as output:
                        for chunk in response.iter_content(128 * 1024):
                            if chunk:
                                check_background_image_space(len(chunk))
                                hasher.update(chunk)
                                output.write(chunk)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
                self.db.execute(
                    """UPDATE images SET local_path=?,mime_type=?,sha256=?,fetched_at=CURRENT_TIMESTAMP
                       WHERE image_id=?""",
                    (rel.as_posix(), mime, hasher.hexdigest(), row["image_id"]),
                )
            finally:
                response.close()

    def migrate_cached_images(self) -> int:
        """Move files created by older versions into asset-specific folders."""
        migrated = 0
        rows = self.db.execute(
            "SELECT * FROM images WHERE local_path IS NOT NULL ORDER BY image_id"
        ).fetchall()
        for row in rows:
            old_rel = Path(row["local_path"])
            old_path = CACHE_ROOT / old_rel
            if not old_path.is_file():
                self.db.execute(
                    "UPDATE images SET local_path=NULL,fetched_at=NULL WHERE image_id=?",
                    (row["image_id"],),
                )
                continue
            new_rel = image_cache_path(row, old_path.suffix or ".img")
            if new_rel == old_rel:
                continue
            new_path = CACHE_ROOT / new_rel
            new_path.parent.mkdir(parents=True, exist_ok=True)
            if not new_path.exists():
                old_path.replace(new_path)
            self.db.execute(
                "UPDATE images SET local_path=? WHERE image_id=?",
                (new_rel.as_posix(), row["image_id"]),
            )
            migrated += 1
        self.db.commit()
        return migrated

    def parse_detail(self, url: str, html: str) -> None:
        soup = BeautifulSoup(html, "html.parser")
        parsed = urlparse(url)
        detail = DETAIL_RE.match(parsed.path)
        episode = EPISODE_RE.match(parsed.path)
        if episode:
            self.parse_episode(url, soup, *episode.groups())
            self.discover_links(soup, url)
            return
        if not detail:
            return
        media_type, media_id = detail.groups()
        if self.db.execute(
            "SELECT 1 FROM hidden_media WHERE media_type=? AND media_id=?", (media_type, media_id)
        ).fetchone():
            return
        h1 = soup.find("h1")
        page_title = (h1.get_text(" ", strip=True) if h1 else "") or (soup.title.get_text(strip=True).split("|")[0].strip() if soup.title else f"{media_type} {media_id}")
        description_meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        description = description_meta.get("content", "") if description_meta else ""
        body_text = soup.get_text(" ", strip=True)
        year_match = YEAR_RE.search(body_text)
        rating_match = re.search(r"\b(\d\.\d)\b", body_text)
        raw = {"url": url, "title": page_title}
        page_props = self.next_page_props(soup)
        details = page_props.get("details") or {}
        if not details and not any(
            obj.get("@type") == ("Movie" if media_type == "movie" else "TVSeries")
            for obj in self.parse_json_data(soup)
        ):
            raise ValueError(f"No {media_type} detail data for {media_id}")

        if details:
            page_title = details.get("title") or page_title
            description = details.get("desc") or description
            year_value = details.get("year")
            rating_value = details.get("score")
            release_date = details.get("release_date")
            certification = details.get("certification")
            duration = details.get("duration")
            runtime_match = RUNTIME_RE.search(str(duration or ""))
            runtime_minutes = ((int(runtime_match.group(1) or 0) * 60) + int(runtime_match.group(2))) if runtime_match else None
            seasons_data = details.get("seasons") or []
            raw.update(details)
        else:
            year_value = int(year_match.group()) if year_match else None
            rating_value = float(rating_match.group(1)) if rating_match else None
            release_date = None
            certification = None
            runtime_minutes = None
            seasons_data = []

        genres: list[str] = list(details.get("categories") or [])
        countries = []
        def add_countries(value):
            if isinstance(value, list):
                for entry in value:
                    add_countries(entry)
            elif isinstance(value, dict):
                add_countries(value.get("name") or value.get("english_name") or
                              value.get("country") or value.get("iso_3166_1"))
            elif isinstance(value, str):
                for part in value.split(","):
                    country = part.strip()
                    if country and len(country) <= 80 and country not in countries:
                        countries.append(country)
        for field in ("countries", "country", "production_countries", "origin_country", "countryOfOrigin"):
            add_countries(details.get(field))
        for obj in self.parse_json_data(soup):
            if obj.get("@type") in {"Movie", "TVSeries"} or obj.get("name") == page_title:
                description = obj.get("description") or description
                genre = obj.get("genre") or []
                genres = [genre] if isinstance(genre, str) else genre
                raw.update(obj)
                for field in ("countries", "country", "production_countries", "origin_country", "countryOfOrigin"):
                    add_countries(obj.get(field))
                break

        if not self.values_allowed(year_value, rating_value, genres):
            print(f"FILTERED OUT {media_type}/{media_id}: {page_title}")
            return

        self.db.execute(
            """INSERT INTO media(media_type,media_id,title,description,year,rating,source_url,raw_json,fetched_at)
               VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(media_type,media_id) DO UPDATE SET
                 title=excluded.title,description=excluded.description,year=excluded.year,
                 rating=excluded.rating,source_url=excluded.source_url,raw_json=excluded.raw_json,
                 fetched_at=CURRENT_TIMESTAMP""",
            (media_type, media_id, page_title, description, year_value,
             rating_value, url, json.dumps(raw, ensure_ascii=False)),
        )
        self.db.execute(
            """UPDATE media SET release_date=?,runtime_minutes=?,certification=?,seasons_count=?
               WHERE media_type=? AND media_id=?""",
            (release_date, runtime_minutes, certification, len(seasons_data) or None, media_type, media_id),
        )
        if countries:
            self.db.execute("DELETE FROM media_countries WHERE media_type=? AND media_id=?", (media_type, media_id))
            self.db.executemany(
                "INSERT INTO media_countries(media_type,media_id,country) VALUES(?,?,?)",
                [(media_type, media_id, country) for country in countries],
            )
        for genre_name in genres:
            self.db.execute("INSERT OR IGNORE INTO genres(name) VALUES(?)", (genre_name,))
            self.db.execute(
                """INSERT OR IGNORE INTO media_genres(media_type,media_id,genre_id)
                   SELECT ?,?,genre_id FROM genres WHERE name=?""",
                (media_type, media_id, genre_name),
            )

        for image_type, key in (("poster", "poster"), ("backdrop", "background"), ("logo", "logo")):
            self.record_image(media_type, media_id, image_type, normal_image_url(details.get(key, "")))

        for order, actor in enumerate(details.get("actors") or []):
            name = actor.get("name") or ""
            if not name:
                continue
            person_key = str(actor.get("id") or re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-"))
            self.db.execute(
                """INSERT INTO people(person_key,name) VALUES(?,?)
                   ON CONFLICT(person_key) DO UPDATE SET name=excluded.name""",
                (person_key, name),
            )
            self.db.execute(
                """INSERT OR IGNORE INTO credits(media_type,media_id,person_key,character_name,credit_order)
                   VALUES(?,?,?,?,?)""",
                (media_type, media_id, person_key, actor.get("label"), order),
            )
            self.record_image("person", person_key, "person", normal_image_url(actor.get("avatar", "")))

        for position, related in enumerate(details.get("recommendations") or []):
            related_id = str(related.get("id") or "")
            related_type = related.get("mediaType") or related.get("media_type") or media_type
            if not related_id.isdigit() or related_type not in {"movie", "tv"}:
                continue
            self.enqueue(f"/{related_type}/{related_id}", url)
            related_title = related.get("title") or related.get("name") or f"{related_type.title()} {related_id}"
            related_date = related.get("release_date") or related.get("first_air_date") or ""
            related_year = int(str(related_date)[:4]) if str(related_date)[:4].isdigit() else related.get("year")
            self.db.execute(
                """UPDATE media SET title=COALESCE(NULLIF(?,''),title),
                   year=COALESCE(?,year),rating=COALESCE(?,rating)
                   WHERE media_type=? AND media_id=?""",
                (related_title, related_year, related.get("rating") or related.get("score"), related_type, related_id),
            )
            self.db.execute(
                """INSERT OR IGNORE INTO recommendations(media_type,media_id,related_type,related_id,position)
                   VALUES(?,?,?,?,?)""",
                (media_type, media_id, related_type, related_id, position),
            )
            self.record_image(related_type, related_id, "poster", normal_image_url(related.get("poster", "")))

        if media_type == "tv":
            for season in seasons_data:
                season_number = int(season.get("season_number") or 0)
                episode_count = int(season.get("episode_count") or len(season.get('episodes') or []))
                self.db.execute(
                    """INSERT INTO seasons(show_id,season_number,title,episode_count)
                       VALUES(?,?,?,?) ON CONFLICT(show_id,season_number) DO UPDATE SET
                       title=excluded.title,episode_count=excluded.episode_count""",
                    (media_id, season_number, season.get("name"), episode_count),
                )
                for episode_number in range(1, episode_count + 1):
                    episode_url = urljoin(BASE_URL, f"/tv/{media_id}/{season_number}/{episode_number}")
                    self.db.execute(
                        """INSERT OR IGNORE INTO episodes
                           (show_id,season_number,episode_number,title,source_url,raw_json)
                           VALUES(?,?,?,?,?,'{}')""",
                        (media_id, season_number, episode_number, f"Episode {episode_number}", episode_url),
                    )
                    self.enqueue(episode_url, url)
                # Some detail responses include the full episode list. Keep its
                # episode-specific overview, air date and score without waiting
                # for individual episode pages to be fetched.
                for item in season.get('episodes') or []:
                    if not isinstance(item, dict):
                        continue
                    try:
                        number = int(item.get('episode_number') or item.get('number') or 0)
                    except (TypeError, ValueError):
                        continue
                    if number < 1:
                        continue
                    episode_url = urljoin(BASE_URL, f'/tv/{media_id}/{season_number}/{number}')
                    episode_description = item.get('overview') or item.get('synopsis') or item.get('description') or ''
                    if episode_description.strip() == (description or '').strip():
                        episode_description = ''
                    date = item.get('air_date') or item.get('first_air_date') or item.get('release_date') or ''
                    date = str(date)[:10] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(date)[:10]) else None
                    self.db.execute('''INSERT INTO episodes
                        (show_id,season_number,episode_number,title,description,air_date,runtime_minutes,rating,source_url,raw_json)
                        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(show_id,season_number,episode_number) DO UPDATE SET
                        title=CASE WHEN excluded.title='' THEN episodes.title ELSE excluded.title END,
                        description=CASE WHEN excluded.description='' THEN episodes.description ELSE excluded.description END,
                        air_date=COALESCE(excluded.air_date,episodes.air_date),
                        runtime_minutes=COALESCE(excluded.runtime_minutes,episodes.runtime_minutes),
                        rating=COALESCE(excluded.rating,episodes.rating)''',
                        (media_id, season_number, number, item.get('name') or item.get('title') or '',
                         episode_description, date,
                         episode_runtime_minutes(item.get('runtime') or item.get('duration')),
                         episode_rating(item.get('imdb_rating') or item.get('vote_average') or item.get('rating')),
                         episode_url, json.dumps(item, ensure_ascii=False)))

        cast_heading = soup.find(lambda tag: tag.name in {"h2", "h3"} and "cast" in tag.get_text(" ", strip=True).casefold())
        cast_images = set()
        if cast_heading:
            for element in cast_heading.find_all_next():
                if element.name in {"h2", "h3"} and "you may like" in element.get_text(" ", strip=True).casefold():
                    break
                if element.name == "img":
                    cast_images.add(id(element))
        for order, img in enumerate(soup.find_all("img")):
            src = best_src(img)
            in_cast = id(img) in cast_images
            anchor = img.find_parent("a", href=True)
            in_episode = bool(anchor and EPISODE_RE.match(urlparse(urljoin(BASE_URL, anchor["href"])).path))
            image_type = self.classify_image(img, page_title, in_cast, in_episode)
            alt = (img.get("alt") or "").strip()
            if image_type == "person" and alt:
                if details.get("actors"):
                    continue
                person_key = re.sub(r"[^a-z0-9]+", "-", alt.casefold()).strip("-")
                self.db.execute("INSERT OR IGNORE INTO people(person_key,name) VALUES(?,?)", (person_key, alt))
                self.db.execute(
                    """INSERT OR IGNORE INTO credits(media_type,media_id,person_key,credit_order)
                       VALUES(?,?,?,?)""",
                    (media_type, media_id, person_key, order),
                )
                self.record_image("person", person_key, "person", src)
            elif anchor and DETAIL_RE.match(urlparse(urljoin(BASE_URL, anchor["href"])).path):
                related_type, related_id = DETAIL_RE.match(urlparse(urljoin(BASE_URL, anchor["href"])).path).groups()
                self.record_image(related_type, related_id, "poster", src)
                self.db.execute(
                    """INSERT OR IGNORE INTO recommendations(media_type,media_id,related_type,related_id,position)
                       VALUES(?,?,?,?,?)""",
                    (media_type, media_id, related_type, related_id, order),
                )
            else:
                self.record_image(media_type, media_id, image_type, src)

        self.discover_links(soup, url)

    def parse_episode(self, url: str, soup: BeautifulSoup, show_id: str, season: str, episode: str) -> None:
        props = self.next_page_props(soup)
        details = props.get("details") or {}
        if not isinstance(details, dict):
            details = {}
        episode_info = details.get("episodeInfo") or {}
        if not isinstance(episode_info, dict):
            episode_info = {}
        # The episode route can expose its own fields directly in details. The
        # generic page description is often the *series* synopsis.
        schema_episode = next((item for item in self.parse_json_data(soup)
                               if item.get('@type') == 'TVEpisode'), {})
        sources = (episode_info, schema_episode, details)
        def field(*keys):
            return next((source[key] for source in sources for key in keys
                         if source.get(key) not in (None, '')), None)
        date_value = field('air_date', 'airDate', 'first_air_date', 'firstAirDate',
                           'release_date', 'datePublished', 'date')
        air_date = str(date_value)[:10] if date_value and re.fullmatch(
            r'\d{4}-\d{2}-\d{2}', str(date_value)[:10]) else None
        rating_info = schema_episode.get('aggregateRating') or {}
        if not isinstance(rating_info, dict):
            rating_info = {}
        rating = next((score for score in (
            episode_rating(episode_info.get(key)) for key in
            ('imdb_rating', 'imdbRating', 'imdb_score', 'imdbScore', 'vote_average', 'voteAverage', 'rating', 'score'))
            if score is not None), None)
        if rating is None:
            rating = episode_rating(rating_info.get('ratingValue'))
        if rating is None:
            rating = episode_rating(field('imdb_rating', 'imdbRating', 'vote_average', 'voteAverage'))
        runtime = next((minutes for minutes in (
            episode_runtime_minutes(value) for value in (
                episode_info.get('runtime'), episode_info.get('duration'),
                episode_info.get('runtime_minutes'), schema_episode.get('duration'),
                field('runtime', 'runtime_minutes', 'duration')))
            if minutes is not None), None)
        title = field('name', 'title') or f'Episode {episode}'
        description = field('overview', 'synopsis', 'plot', 'desc', 'description') or ''
        if not isinstance(description, str):
            description = ''
        series_row = self.db.execute("SELECT description FROM media WHERE media_type='tv' AND media_id=?", (show_id,)).fetchone()
        series_description = (series_row['description'] or '').strip() if series_row else ''
        if description.strip() == series_description:
            description = ''
        self.db.execute("INSERT OR IGNORE INTO seasons(show_id,season_number) VALUES(?,?)", (show_id, int(season)))
        self.db.execute(
            """INSERT INTO episodes(show_id,season_number,episode_number,title,description,air_date,
               runtime_minutes,rating,source_url,raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(show_id,season_number,episode_number) DO UPDATE SET
               title=CASE WHEN excluded.title='Episode ' || excluded.episode_number
                          THEN episodes.title ELSE excluded.title END,
               description=CASE WHEN excluded.description='' THEN episodes.description
                                ELSE excluded.description END,
               air_date=COALESCE(excluded.air_date,episodes.air_date),
               runtime_minutes=COALESCE(excluded.runtime_minutes,episodes.runtime_minutes),
               rating=COALESCE(excluded.rating,episodes.rating),source_url=excluded.source_url,
               raw_json=excluded.raw_json""",
            (show_id, int(season), int(episode), title, description, air_date, runtime, rating, url,
             json.dumps({"url": url, **episode_info}, ensure_ascii=False)),
        )
        if series_description:
            self.db.execute('''UPDATE episodes SET description='' WHERE show_id=? AND
                season_number=? AND episode_number=? AND TRIM(description)=?''',
                (show_id, int(season), int(episode), series_description))
        owner_key = f"{show_id}:{season}:{episode}"
        for img in soup.find_all("img"):
            self.record_image("episode", owner_key, "episode", best_src(img))

    def run(self, max_pages: int) -> None:
        completed = 0
        while completed < max_pages:
            if self.media_type == "all":
                row = self.db.execute(
                    "SELECT url FROM crawl_queue WHERE status IN ('pending','error') AND attempts < 4 ORDER BY attempts,updated_at LIMIT 1"
                ).fetchone()
            else:
                pattern = "%/movie/%" if self.media_type == "movie" else "%/tv/%"
                row = self.db.execute(
                    """SELECT url FROM crawl_queue WHERE status IN ('pending','error')
                       AND attempts < 4 AND url LIKE ? ORDER BY attempts,updated_at LIMIT 1""",
                    (pattern,),
                ).fetchone()
            if not row:
                break
            url = row["url"]
            self.db.execute("UPDATE crawl_queue SET status='running',attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE url=?", (url,))
            self.db.commit()
            try:
                html = self.fetch(url)
                self.parse_detail(url, html)
                self.db.execute("UPDATE crawl_queue SET status='done',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE url=?", (url,))
                completed += 1
                print(f"[{completed}/{max_pages}] {url}")
            except Exception as exc:
                self.db.execute("UPDATE crawl_queue SET status='error',last_error=?,updated_at=CURRENT_TIMESTAMP WHERE url=?", (str(exc)[:500], url))
                print(f"ERROR {url}: {exc}")
            self.db.commit()
            time.sleep(self.delay)

        if self.download_images:
            rows = [row for row in self.db.execute(
                "SELECT * FROM images WHERE local_path IS NULL ORDER BY image_id"
            ).fetchall() if self.image_allowed(row)]
            for index, image in enumerate(rows, 1):
                try:
                    self.cache_image(image)
                    self.db.commit()
                    print(f"image {index}/{len(rows)} {image['source_url']}")
                except Exception as exc:
                    print(f"IMAGE ERROR {image['source_url']}: {exc}")
                time.sleep(self.delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and refresh the local Movy media catalogue")
    parser.add_argument("--max-pages", type=int, default=100, help="Maximum detail/episode pages per run")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--no-images", action="store_true", help="Collect image URLs without downloading files")
    parser.add_argument("--reset-errors", action="store_true")
    parser.add_argument("--skip-discovery", action="store_true", help="Skip home/listing/sitemap ID discovery")
    args = parser.parse_args()

    connection = connect()
    seeded = seed_catalog(connection)
    for seed in ("/movie/969681", "/tv/97546"):
        connection.execute("INSERT OR IGNORE INTO crawl_queue(url,discovered_from) VALUES(?,?)", (urljoin(BASE_URL, seed), "built-in seed"))
    if args.reset_errors:
        connection.execute("UPDATE crawl_queue SET status='pending',last_error=NULL WHERE status='error'")
    connection.commit()
    print(f"Seeded {seeded} known catalogue records")
    sync = MovySync(args.delay, not args.no_images, args.timeout)
    if not args.skip_discovery:
        sync.discover_seed_pages()
    sync.run(args.max_pages)


if __name__ == "__main__":
    main()

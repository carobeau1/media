#!/usr/bin/env python3
"""Open a web page in Chrome and print streaming-video URLs as they appear.

Use only on pages and streams you are authorized to inspect.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options
    SELENIUM_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    webdriver = None
    Options = None
    WebDriverException = Exception
    SELENIUM_IMPORT_ERROR = exc


MEDIA_EXTENSIONS = (
    ".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mov", ".mkv", 
    ".ts", ".m4s", ".aac", ".mp3", ".flv",
)
MANIFEST_EXTENSIONS = (".m3u8", ".mpd")
SEGMENT_EXTENSIONS = (".ts", ".m4s", ".aac")
MEDIA_MIME_PREFIXES = ("video/", "audio/")
MEDIA_MIME_TYPES = (
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
    "application/mp4",
    "application/octet-stream",
)
SOURCE_CONFIG_PATH = Path(__file__).resolve().with_name("download-config.json")


def clean_mime(value):
    return (value or "").split(";", 1)[0].strip().lower()


def path_extension_matches(url, extensions):
    try:
        return urlparse(url).path.lower().endswith(extensions)
    except ValueError:
        return False


def classify(url, mime="", resource_type="", include_segments=False):
    if not url or url.startswith("data:"):
        return None
    lower_url = url.lower()
    mime = clean_mime(mime)

    if lower_url.startswith("blob:"):
        return "blob (page-local; look for a manifest below)"
    if path_extension_matches(url, MANIFEST_EXTENSIONS):
        return "manifest"
    if path_extension_matches(url, SEGMENT_EXTENSIONS):
        return "segment" if include_segments else None
    if path_extension_matches(url, MEDIA_EXTENSIONS):
        return "media"
    if mime.startswith(MEDIA_MIME_PREFIXES):
        return "media"
    if mime in MEDIA_MIME_TYPES:
        # Octet-stream is too broad unless Chrome calls it media.
        if mime != "application/octet-stream" or resource_type == "Media":
            return "media"
    if resource_type == "Media":
        return "media"
    return None


DOM_SCAN_SCRIPT = r"""
const results = [];
const seenRoots = new Set();
function walk(root) {
  if (!root || seenRoots.has(root)) return;
  seenRoots.add(root);
  const nodes = root.querySelectorAll('video, audio, source, track, iframe, embed, object');
  for (const el of nodes) {
    for (const attr of ['src', 'currentSrc', 'data']) {
      let value = attr === 'currentSrc' ? el.currentSrc : el.getAttribute(attr);
      if (value) results.push({url: value, tag: el.tagName.toLowerCase(), attr});
    }
  }
  for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) walk(el.shadowRoot);
  }
}
walk(document);
for (const entry of performance.getEntriesByType('resource')) {
  results.push({url: entry.name, tag: 'performance', attr: entry.initiatorType || 'resource'});
}
return results;
"""


class Reporter:
    def __init__(self, include_segments=False):
        self.include_segments = include_segments
        self.seen = set()
        self.counts = {}
        self.first_stream_url = None
        self.stream_urls = []

    def report(self, url, source, mime="", resource_type=""):
        if not url:
            return
        kind = classify(url, mime, resource_type, self.include_segments)
        if not kind or url in self.seen:
            return
        self.seen.add(url)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        print(f"\n[{kind}] [{source}]\n{url}", flush=True)
        if kind in ("manifest", "media"):
            self.stream_urls.append(url)
            if self.first_stream_url is None:
                self.first_stream_url = url


def process_performance_logs(driver, reporter):
    for item in driver.get_log("performance"):
        try:
            message = json.loads(item["message"])["message"]
            method = message.get("method")
            params = message.get("params", {})
            if method == "Network.responseReceived":
                response = params.get("response", {})
                reporter.report(
                    response.get("url", ""),
                    "network-response",
                    response.get("mimeType", ""),
                    params.get("type", ""),
                )
            elif method == "Network.requestWillBeSent":
                request = params.get("request", {})
                reporter.report(
                    request.get("url", ""),
                    "network-request",
                    request.get("headers", {}).get("Content-Type", ""),
                    params.get("type", ""),
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue


def scan_dom(driver, reporter):
    try:
        page_url = driver.current_url
        for item in driver.execute_script(DOM_SCAN_SCRIPT):
            raw_url = item.get("url", "")
            url = raw_url if raw_url.startswith(("blob:", "http://", "https://")) else urljoin(page_url, raw_url)
            reporter.report(url, f"DOM:{item.get('tag')}:{item.get('attr')}")
    except WebDriverException:
        pass


def build_driver(args):
    options = Options()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    options.add_argument("--autoplay-policy=no-user-gesture-required")
    options.add_argument("--disable-features=PreloadMediaEngagementData,MediaEngagementBypassAutoplayPolicies")
    options.add_argument("--window-size=1400,900")
    if args.headless:
        options.add_argument("--headless=new")
    if args.user_data_dir:
        options.add_argument(f"--user-data-dir={args.user_data_dir}")
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Network.enable", {})
    detected_user_agent = driver.execute_script("return navigator.userAgent")
    regular_user_agent = detected_user_agent.replace("HeadlessChrome/", "Chrome/")
    driver.execute_cdp_cmd(
        "Network.setUserAgentOverride",
        {"userAgent": regular_user_agent, "acceptLanguage": "en-US,en;q=0.9"},
    )
    return driver, regular_user_agent


def parse_season_episode(page_url):
    parts = [unquote(part) for part in urlparse(page_url).path.split("/") if part]
    if len(parts) < 4 or parts[-4].lower() != "tv":
        raise ValueError("URL path must end with /tv/<series-id>/<series-no>/<episode-no>")
    try:
        season = int(parts[-2])
        episode = int(parts[-1])
    except ValueError as exc:
        raise ValueError("Series and episode numbers in the URL must be integers") from exc
    if season < 0 or episode < 0:
        raise ValueError("Series and episode numbers cannot be negative")
    return season, episode


def parse_page_type(page_url):
    parts = [unquote(part) for part in urlparse(page_url).path.split("/") if part]
    if len(parts) >= 2 and parts[-2].lower() == "movie":
        return "movie", parts[-1], None, None
    if len(parts) >= 4 and parts[-4].lower() == "tv":
        season, episode = parse_season_episode(page_url)
        return "tv", parts[-3], season, episode
    raise ValueError(
        "URL path must end with /movie/<movie-id> or "
        "/tv/<series-id>/<series-no>/<episode-no>"
    )


def load_source_config(config_path=SOURCE_CONFIG_PATH):
    try:
        with Path(config_path).open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Source configuration file not found: {config_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read source configuration: {exc}") from exc

    sources = config.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("sources must be a non-empty object in download-config.json")
    default_source = config.get("default_source")
    if not isinstance(default_source, str) or default_source not in sources:
        raise ValueError("default_source must name an entry in sources")
    cloud_source = config.get("cloud_play_source", default_source)
    if not isinstance(cloud_source, str) or cloud_source not in sources:
        raise ValueError("cloud_play_source must name an entry in sources")
    for name, source in sources.items():
        if not isinstance(name, str) or not name or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ValueError("Source names must contain only letters, numbers, _ or -")
        if not isinstance(source, dict):
            raise ValueError(f"sources.{name} must be an object")
        for kind, tokens in (
            ("movie", ("<movie-id>",)),
            ("tv", ("<series-id>", "<series-no>", "<episode-no>")),
        ):
            template = source.get(f"{kind}_url_template")
            if template is None:
                continue  # A source may support movies or TV only.
            if not isinstance(template, str) or not all(token in template for token in tokens):
                raise ValueError(f"sources.{name}.{kind}_url_template must include {', '.join(tokens)}")
            parsed = urlparse(template)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"sources.{name}.{kind}_url_template must be an absolute HTTP URL")
        for kind, tokens in (
            ("movie", ("<movie-id>",)),
            ("tv", ("<series-id>", "<series-no>", "<episode-no>")),
        ):
            template = source.get(f"{kind}_cloud_url_template")
            if template is None:
                continue
            if not isinstance(template, str) or not all(token in template for token in tokens):
                raise ValueError(f"sources.{name}.{kind}_cloud_url_template must include {', '.join(tokens)}")
            parsed = urlparse(template)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"sources.{name}.{kind}_cloud_url_template must be an absolute HTTP URL")
        if not any(source.get(f"{kind}_url_template") for kind in ("movie", "tv")):
            raise ValueError(f"sources.{name} needs a movie or TV URL template")
    if type(config.get('headless')) is not bool:
        raise ValueError('headless must be true or false in download-config.json')
    end_delay = config.get('end_delay_seconds')
    if type(end_delay) not in (int, float) or not 0 <= end_delay <= 86400:
        raise ValueError('end_delay_seconds must be between 0 and 86400')
    for key, required in (
        ('movie_download_path', ('<movie-id>', '<title>', '<year-published>')),
        ('tv_download_path', ('<series-id>', '<series-number>', '<episode-number>', '<title>', '<publication-year>')),
    ):
        value = config.get(key)
        if not isinstance(value, str) or not all(token in value for token in required):
            raise ValueError(f'{key} must include: {", ".join(required)}')
        normalized = value.replace('\\', '/')
        if normalized.startswith('/') or '..' in normalized.split('/') or ':' in normalized or not normalized.startswith('media/'):
            raise ValueError(f'{key} must be a relative path inside media/')
    return config


def load_source_templates(config_path=SOURCE_CONFIG_PATH, source=None):
    config = load_source_config(config_path)
    name = source or config['default_source']
    if name not in config['sources']:
        raise ValueError(f"Unknown source {name!r}; available: {', '.join(config['sources'])}")
    entry = config['sources'][name]
    return entry.get('movie_url_template'), entry.get('tv_url_template')


def build_source_url(media_type, identifiers, config_path=SOURCE_CONFIG_PATH, source=None):
    movie_template, tv_template = load_source_templates(config_path, source)
    if media_type == "movie":
        if len(identifiers) != 1:
            raise ValueError("Usage: download.py movie <movie-id>")
        if not movie_template:
            raise ValueError(f"Source {source or load_source_config(config_path)['default_source']!r} does not support movies")
        return movie_template.replace("<movie-id>", quote(identifiers[0], safe=""))

    if len(identifiers) != 3:
        raise ValueError("Usage: download.py tv <series-id> <series-no> <episode-no>")
    if not tv_template:
        raise ValueError(f"Source {source or load_source_config(config_path)['default_source']!r} does not support TV")
    series_id, series_no, episode_no = identifiers
    try:
        if int(series_no) < 0 or int(episode_no) < 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Series and episode numbers must be non-negative integers") from exc
    return (tv_template.replace("<series-id>", quote(series_id, safe=""))
            .replace("<series-no>", quote(series_no, safe=""))
            .replace("<episode-no>", quote(episode_no, safe="")))


def build_cloud_url(media_type, identifiers, config_path=SOURCE_CONFIG_PATH, source_name=None):
    config = load_source_config(config_path)
    selected = source_name or config.get('cloud_play_source', config['default_source'])
    if selected not in config['sources']:
        raise ValueError(f'Unknown cloud source: {selected}')
    source = config['sources'][selected]
    key = f'{media_type}_cloud_url_template'
    template = source.get(key) or source.get(f'{media_type}_url_template')
    if not template:
        raise ValueError(f"Cloud source has no {media_type} URL template")
    if media_type == 'movie':
        if len(identifiers) != 1:
            raise ValueError('Movie playback requires a movie ID')
        return template.replace('<movie-id>', quote(str(identifiers[0]), safe=''))
    if media_type == 'tv' and len(identifiers) == 3:
        series_id, season, episode = identifiers
        return (template.replace('<series-id>', quote(str(series_id), safe=''))
                .replace('<series-no>', quote(str(season), safe=''))
                .replace('<episode-no>', quote(str(episode), safe='')))
    raise ValueError('TV playback requires a series ID, season and episode')


def safe_windows_filename(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "Unknown Series"


def title_before_separator(page_title):
    return safe_windows_filename(page_title.split("|", 1)[0].strip())


def tv_output_filename(page_title, season, episode):
    series_title = title_before_separator(page_title)
    return f"{series_title} S{season:02d}E{episode:02d}.mp4"


def tv_output_path(page_title, series_id, season, episode):
    return configured_output_path('tv', page_title, series_id, season=season, episode=episode)


def movie_output_filename(page_title, year, movie_id):
    movie_title = title_before_separator(page_title)
    safe_movie_id = safe_windows_filename(movie_id)
    safe_year = safe_windows_filename(str(year))
    return f"{movie_title} [{safe_year}] [{safe_movie_id}].mp4"


def movie_output_path(page_title, year, movie_id):
    return configured_output_path('movie', page_title, movie_id, year=year)


def configured_output_path(kind, page_title, media_id, year=None, season=None, episode=None):
    config = load_source_config()
    template = config['movie_download_path' if kind == 'movie' else 'tv_download_path']
    values = {
        '<title>': title_before_separator(page_title),
        '<movie-id>': safe_windows_filename(str(media_id)),
        '<series-id>': safe_windows_filename(str(media_id)),
        '<year-published>': safe_windows_filename(str(year or 'Unknown Year')),
        '<publication-year>': safe_windows_filename(str(year or 'Unknown Year')),
        '<series-number>': safe_windows_filename(str(season)),
        '<episode-number>': safe_windows_filename(str(episode)),
    }
    for token, value in values.items():
        template = template.replace(token, value)
    return Path(template.replace('\\', '/'))


def get_series_year(driver):
    """Use the series year from page data, if available."""
    match = re.search(r'"year"\s*:\s*"?((?:19|20)\d{2})', driver.page_source)
    if match:
        return match.group(1)
    match = re.search(r'"(?:first_air_date|release_date|datePublished)"\s*:\s*"(\d{4})-', driver.page_source)
    return match.group(1) if match else None


def find_date_published(value, require_movie=True):
    if isinstance(value, list):
        for item in value:
            found = find_date_published(item, require_movie)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None

    item_type = value.get("@type", "")
    types = item_type if isinstance(item_type, list) else [item_type]
    is_movie = any(str(item).lower() == "movie" for item in types)
    published = value.get("datePublished")
    if published and (is_movie or not require_movie):
        match = re.search(r"\b(\d{4})\b", str(published))
        if match:
            return match.group(1)

    for child in value.values():
        found = find_date_published(child, require_movie)
        if found:
            return found
    return None


def get_movie_year(driver, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        texts = driver.execute_script(
            "return Array.from(document.querySelectorAll(" 
            "'script[type=\"application/ld+json\"]')).map(el => el.textContent);"
        )
        parsed_values = []
        for text in texts:
            try:
                parsed_values.append(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

        for require_movie in (True, False):
            for value in parsed_values:
                year = find_date_published(value, require_movie=require_movie)
                if year:
                    return year

        # Fallback for malformed or non-standard JSON-LD embedded in source.
        match = re.search(r'["\']datePublished["\']\s*:\s*["\'](\d{4})', driver.page_source)
        if match:
            return match.group(1)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)


def find_yt_dlp():
    executable = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if executable:
        return executable
    return "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"


def read_choice_with_timeout(timeout):
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return ""

    print(f"Choose a URL number within {timeout} seconds [default 1]: ", end="", flush=True)
    if sys.platform == "win32":
        import msvcrt

        characters = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            character = msvcrt.getwch()
            if character in ("\r", "\n"):
                print()
                return "".join(characters)
            if character == "\b":
                if characters:
                    characters.pop()
                    print("\b \b", end="", flush=True)
            elif character.isdigit():
                characters.append(character)
                print(character, end="", flush=True)
        print()
        return "".join(characters)

    import select

    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if ready:
        return sys.stdin.readline().strip()
    print()
    return ""


def choose_stream_url(stream_urls, timeout=5):
    if not stream_urls:
        return None
    if len(stream_urls) == 1:
        print("\nUsing the only downloadable stream URL found.")
        return stream_urls[0]

    print(f"\nFound {len(stream_urls)} downloadable stream URLs:")
    for number, url in enumerate(stream_urls, start=1):
        print(f"  {number}. {url}")

    answer = read_choice_with_timeout(timeout)
    try:
        selection = int(answer) if answer else 1
    except ValueError:
        selection = 1
    if not 1 <= selection <= len(stream_urls):
        selection = 1
    print(f"Using stream URL {selection}.")
    return stream_urls[selection - 1]


def download_stream(stream_url, filename, referer_url, user_agent, threads=1):
    output_path = Path(filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filename = str(output_path)
    command = [
        find_yt_dlp(),
        "--cookies", "cookies.txt",
        "--referer", referer_url,
        "--user-agent", user_agent,
        "-N", str(threads),
        "-o", filename,
        stream_url,
    ]
    print(f"\nDownloading first stream as: {filename}")
    print("Running: " + subprocess.list2cmdline(command), flush=True)
    try:
        completed = subprocess.run(command, check=False)
    except FileNotFoundError:
        print("Error: yt-dlp was not found in the current directory or PATH.", file=sys.stderr)
        return 1
    if completed.returncode:
        print(f"yt-dlp.exe failed with exit code {completed.returncode}.", file=sys.stderr)
    return completed.returncode


def parse_args():
    parser = argparse.ArgumentParser(
        description="Find and download a configured movie or TV episode stream."
    )
    parser.add_argument("media_type", choices=("movie", "tv"), help="Media type to download")
    parser.add_argument("--source", help="Named source from download-config.json (default: default_source)")
    parser.add_argument("--ignore-metadata-errors", action="store_true",
                        help="Use year 1900 when a movie publication year cannot be found")
    parser.add_argument(
        "identifiers", nargs="+",
        help="movie: <movie-id>; tv: <series-id> <series-no> <episode-no>",
    )
    parser.add_argument(
        "--duration", type=float, default=0,
        help="Seconds to scan; 0 keeps scanning until Ctrl+C (default: 0)",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="Scan interval in seconds (default: 1)")
    parser.add_argument("--download-threads", type=int, default=4,
                        help="Concurrent yt-dlp fragments (-N), 1 to 16 (default: 4)")
    headless = parser.add_mutually_exclusive_group()
    headless.add_argument("--headless", dest="headless", action="store_true", default=None,
                          help="Run Chrome without a visible window (overrides JSON)")
    headless.add_argument("--no-headless", dest="headless", action="store_false",
                          help="Show Chrome even when JSON defaults to headless")
    parser.add_argument("--segments", action="store_true", help="Also print .ts/.m4s/.aac segment URLs (very noisy)")
    parser.add_argument(
        "--user-data-dir",
        help="Optional Chrome profile directory for pages requiring an existing login",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not 1 <= args.download_threads <= 16:
        print('Error: --download-threads must be between 1 and 16', file=sys.stderr)
        return 2
    try:
        config = load_source_config()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.headless is None:
        args.headless = config['headless']
    if SELENIUM_IMPORT_ERROR:
        print("Selenium is not installed. Run:  py -m pip install selenium", file=sys.stderr)
        return 2
    try:
        page_url = build_source_url(args.media_type, args.identifiers, source=args.source)
        page_type = args.media_type
        media_id = args.identifiers[0]
        season, episode = (int(args.identifiers[1]), int(args.identifiers[2])) if page_type == 'tv' else (None, None)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    reporter = Reporter(include_segments=args.segments)
    driver = None
    filename = None
    user_agent = None
    try:
        driver, user_agent = build_driver(args)
        print(f"Opening: {page_url}")
        print(f"Browser user agent: {user_agent}")
        print("Start the video if it does not autoplay. Press Ctrl+C to stop.\n")
        driver.get(page_url)
        if page_type == "tv":
            filename = configured_output_path('tv', driver.title, media_id,
                                              year=get_series_year(driver), season=season, episode=episode)
        else:
            year = get_movie_year(driver)
            if not year:
                if not args.ignore_metadata_errors:
                    raise ValueError("Could not find a datePublished year for this movie")
                year = '1900'
                print("Movie publication year unavailable; using 1900 (--ignore-metadata-errors).")
            filename = movie_output_path(driver.title, year, media_id)
        print(f"Page title: {driver.title}")
        print(f"Output file: {filename}")
        started = time.monotonic()
        while args.duration <= 0 or time.monotonic() - started < args.duration:
            process_performance_logs(driver, reporter)
            scan_dom(driver, reporter)
            if reporter.first_stream_url:
                print("\nFirst usable stream URL found.")
                break
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.")
    except WebDriverException as exc:
        print(f"\nChrome/Selenium error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"\nPage metadata error: {exc}", file=sys.stderr)
        return 1
    finally:
        if driver:
            process_performance_logs(driver, reporter)
            driver.quit()

    total = len(reporter.seen)
    summary = ", ".join(f"{key}={value}" for key, value in sorted(reporter.counts.items()))
    print(f"\nFound {total} unique media URL(s)" + (f": {summary}" if summary else "."))
    if not reporter.stream_urls:
        print("No downloadable stream URL was detected.", file=sys.stderr)
        return 1
    selected_stream_url = choose_stream_url(reporter.stream_urls, timeout=5)
    result = download_stream(selected_stream_url, filename, page_url, user_agent, args.download_threads)
    end_delay = config['end_delay_seconds']
    if end_delay:
        print(f"\nDownload command finished. Keeping this window open for {end_delay:g} seconds.")
        print("Press Ctrl+C to close it sooner.")
        try:
            time.sleep(end_delay)
        except KeyboardInterrupt:
            print("\nClosing.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())

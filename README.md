# Media home backend template

This project keeps the supplied homepage snapshot intact and adds a Flask-backed catalogue search.

## Title right-click menu

Right-click a movie title or poster and choose **Download** to add it to the
server queue. On a TV title, **Download** opens the available episodes; choose
one to queue. Right-clicking an individual episode downloads that episode.
The same menu is available with Shift+F10 or the keyboard Menu key while a
title link has focus.

## Movie downloads

The included `download.py` and `download-config.json` live beside `app.py`.
On a movie detail page, **Download** starts `python -u download.py movie <movie-id>`
on the server. Episode tiles can start `download.py tv <show-id> <season> <episode>`.
The app queue passes `--ignore-metadata-errors` for movie downloads. If the
page has no publication year, the filename uses `1900` and the download proceeds.
Direct `download.py` runs keep strict year checking unless that flag is passed.
The movie and TV output folders are defined by `movie_download_path` and
`tv_download_path` in `download-config.json`. Add another named entry under
`sources` with a `movie_url_template` and/or `tv_url_template`. The placeholders
are `<movie-id>` for movies and `<series-id>`, `<series-no>`, `<episode-no>` for TV.
Set `default_source` to the name used by the app queue. For a direct run, select
another entry with `python download.py movie 123 --source other-name` (or
`python download.py tv 123 1 2 --source other-name`). IDs must be valid on that
source; the script does not translate IDs between sites. Cloud Play on movie
pages uses `cloud_play_source` from the same config. Set that to a named source;
its `movie_cloud_url_template` is used for the iframe. If omitted, Cloud Play
uses that source's `movie_url_template`. The bundled Movy entry retains the
existing Vidy player URL as its cloud template. Change the template in the
config to use another player without editing JavaScript. The movie detail
hero shows the selected source and its movie page URL. The selection is saved
in the signed-in profile and also applies to new movie downloads. The `example`
entry uses `example.invalid` as a placeholder: replace its URLs with a real
source before selecting it for playback or downloads. The title rows appear
only after Down is pressed or the down button is clicked. Download progress appears in the
footer on every page, with SxxExx for TV episodes. The script needs Chrome,
Selenium, yt-dlp on the PATH, and
`cookies.txt` in the app directory. The app uses the same Python interpreter
that runs Flask. In `download-config.json`, `headless` controls the default
browser mode; `--headless` and `--no-headless` override it. `end_delay_seconds`
sets the pause after yt-dlp finishes; zero reports completion immediately.
Settings has a server-wide Download threads field (1 to 16, default 1), used
as yt-dlp's `-N` for the next title. Only one title downloads at a time;
additional requests appear in the footer queue monitor. A cloud icon beside
a movie's classification means there is no completed file in the configured
movie folder; the icon changes when a finished video is present.

The footer queue monitor shows the current download's elapsed time and estimated
time remaining. Use its arrow buttons to reorder waiting items or **Remove** to
discard one. Running items have **Stop & remove** in both the queue drawer and
footer queue panel. It stops the downloader process and its yt-dlp children,
removes the job from the saved queue, and lets the next waiting job start.
Partially downloaded files may remain on disk for a later resume. Pending jobs are stored in `media.db` and resume after a restart.
Stop the server with **Ctrl+C** in the terminal running `python app.py`
(or send SIGTERM on Linux). It stops starting new jobs, finishes the current
download and in-flight metadata writes, then exits. Leave the terminal open
until it prints **Stopped safely.** This can take as long as the current download.

## Run on Ubuntu

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8080` on this computer. Other devices on the same LAN can open
`http://<this-computer's-LAN-IP>:8080`; find that address with `hostname -I` on
Linux or `ipconfig` on Windows. Start with `python app.py`, which listens on all
network interfaces. If a firewall blocks port 8080, allow inbound TCP 8080 on
your private LAN. For example, run this from an Administrator PowerShell on
Windows when the network is set to Private:

```powershell
New-NetFirewallRule -DisplayName "Media Home LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 -Profile Private
```

The app caches images on demand. Background image downloads pause whenever
the app drive has less than 2 GB free and resume when space is available again;
opening a movie or TV page remains interactive. The footer shows when background
image downloads are paused.

TV details register every episode number reported for each season in `episodes`.
The background metadata worker checks incomplete episodes in small batches
alongside title repair, then up to 30 more episodes per completed title pass,
filling release date, runtime in minutes, and episode rating when the source
provides those fields. Unavailable values stay empty and are retried after a day.
The TV page can switch between seasons. The similar-title scan also discovers
movie and TV IDs from their browse pages, checking parental settings before
inserting new titles; the metadata worker then fills their details and episodes.

Pages:

- `/` — homepage
- `/movies` — movies catalogue page
- `/shows` — TV shows catalogue page
- `/tv/<id>` — selected TV-show detail page
- `/movie/<id>` — selected movie detail page
- `/api/search` — backend movie/TV search
- `/api/shows/<id>` — selected show data for later seasons, episodes and cast integration
- `/api/movies/<id>` — selected movie data for later cast and recommendation integration
- `/api/catalog/ids` — current movie-ID and show-ID lists
- `/api/catalog/stats` — database, image-cache and crawl-queue totals

Every HTML page includes a pinned bottom status bar. Its centered counters show
the current numbers of movies, TV shows and people in the local SQLite database.

On detail pages, the top Home control opens `/` and the adjacent back control
uses browser history (falling back to `/` when there is no earlier page). Movie
metadata is forced open directly below the title logo and above the Play, Add to
List and Download controls, using the original template's facts, badge, genres
and description styling.

Genres on movie and TV detail pages are separated with a spaced middle dot.
Cast cards are keyboard-accessible links to `/person/<person-key>`, which lists
every locally stored movie and TV show connected to that person through the
database credits table. The list updates automatically as more titles are scraped.

## Backend contract

`GET /api/search?q=slow&type=all` returns:

```json
{"results":[{"id":"slow-horses","title":"Slow Horses","type":"tv","type_label":"TV Show","year":"2022","rating":"8.0","poster":"...","url":"/tv/slow-horses"}]}
```

Allowed `type` values are `all`, `movie`, and `tv`. Replace `load_catalog()` with your database or external API lookup later. The `/movie/<id>` and `/tv/<id>` routes are intentionally reserved for the detail templates to be supplied later.

The Jinja environment uses `[[ variable ]]` rather than `{{ variable }}` so the captured page's original scripts and styles remain unchanged.

Movie detail pages are database-driven. Opening `/movie/<id>` checks the local
SQLite record and image cache first. If the movie metadata or artwork is missing,
the Flask app fetches `https://www.movy.sx/movie/<id>` through the same parser used
by `build_media_database.py`, saves the metadata, cast and recommendations, caches
the movie/cast images, and then renders the requested movie rather than the sample
movie embedded in the original snapshot.
The selected movie backdrop is applied to both the responsive hero image and its
hero container, and the saved template's loader-only transparency is removed so
the artwork remains visible behind the title logo and metadata.

TV detail pages work the same way. Opening `/tv/<id>` loads the selected show,
seasons, locally available episodes, cast and recommendations from SQLite. If the
main show record or artwork is missing, it is fetched and cached through the same
importer instead of displaying the Ted Lasso snapshot data.
The selected show's current backdrop is also rendered directly into the hero
container on the server; the captured Ted Lasso picture layer is disabled so it
cannot override the selected show's artwork.
For both movie and show detail routes, the server now removes the captured hero
`<picture>` entirely from the outgoing HTML and replaces it with a single image
whose source is the selected database record. Captured preload images are removed
from these responses as well.
The chosen source is taken from the selected title's parsed detail-page
`background` field, so an older incorrectly cached backdrop cannot outrank it.
Detail pages are returned with no-cache headers to prevent an earlier captured
hero from persisting in the browser cache.
The captured trailer `mediaFill` layer is also removed from movie and show detail
responses. That full-opacity video previously sat above the correct backdrop and
made its frozen frame appear to be a hardcoded background image.

## Build the media database and image cache

The importer uses SQLite, resumes from its last queue state, and starts with all IDs already present in `catalog.json`. Each fetched movie/show page can discover additional IDs from links and embedded page data.

At the start of a normal run it also checks the public homepage, movie/show browse pages and sitemap locations for IDs. Use `--skip-discovery` when you only want to continue the existing queue.

```powershell
.\.venv\Scripts\python.exe build_media_database.py
```

Or, if Python is installed system-wide:

```powershell
py build_media_database.py
```

Files created locally:

- `media.db` — movies, shows, genres, people, credits, seasons, episodes, recommendations and crawl state
- `cache\images\covers\movie\<id>\...` and `covers\tv\<id>\...` — movie and show cover/poster images
- `cache\images\backdrops\movie\<id>\...` and `backdrops\tv\<id>\...` — wide background artwork
- `cache\images\logos\movie\<id>\...` and `logos\tv\<id>\...` — transparent title logos
- `cache\images\people\<person-id>\...` — actor, actress and other cast images
- `cache\images\episodes\<show-id>\S01E01\...` — episode still images
- `cache\images\other\...` — images that cannot be reliably classified

These paths are relative to the extracted project folder. On Windows, for example,
`C:\MediaHome\media-home-backend\cache\images\covers\movie\969681\...`.
Each run automatically moves files made by an older version into the new folders. If
a database record points to a missing file, it is marked for download again.

Useful options:

```powershell
# Add movies only
.\.venv\Scripts\python.exe build_media_database.py --type movie

# Add TV shows (and their episodes) only
.\.venv\Scripts\python.exe build_media_database.py --type tv

# Movies from 1990 through 2009 with IMDb rating 7.0 or higher
.\.venv\Scripts\python.exe build_media_database.py --type movie --year-from 1990 --year-to 2009 --rating-min 7

# Comedy OR Drama shows. Repeat --genre or separate alternatives with commas.
.\.venv\Scripts\python.exe build_media_database.py --type tv --genre Comedy,Drama

# Combine all filters
.\.venv\Scripts\python.exe build_media_database.py --type tv --year-from 2015 --year-to 2026 --rating-min 8 --genre Crime --genre Thriller

# Metadata and image URLs only; do not download image files
.\.venv\Scripts\python.exe build_media_database.py --pages 500 --metadata-only

# Retry pages that previously failed
.\.venv\Scripts\python.exe build_media_database.py --pages 500 --retry-errors

# Continue in batches until the queue is empty
.\.venv\Scripts\python.exe build_media_database.py --pages 250 --continuous
```

The year, rating and genre checks happen after each detail page is read, because
genre and IMDb rating are not always present in listing pages. Non-matching pages
are marked complete but their cast, seasons, episodes and images are not added.
Multiple genre values use OR matching: `--genre Comedy --genre Drama` accepts
either genre. Filters affect the current scrape and do not delete titles already
stored by an earlier run.

Stop with `Ctrl+C` at any time. The next run resumes from `crawl_queue`. Keep `--delay 1` or higher to avoid sending excessive requests.

Export plain ID lists whenever needed:

```powershell
.\.venv\Scripts\python.exe export_ids.py
```

This creates `movie-ids.txt` and `show-ids.txt`.

## Delete or import a title

On a movie or show detail page, use **Delete this movie/show** and confirm. This removes its database entry, associated episodes and cached images, unused cast records, catalog entry, and exported ID. You can also run `python delete_media.py tv 61818` or `python delete_media.py movie 157336`. Shared cast images and records remain until no other title uses them.

Opening `/tv/<id>` or `/movie/<id>` for a missing ID fetches its detail page and artwork automatically and adds it to the database and ID list. If the source is unavailable or has no matching detail data, the site responds with 404. Opening a deleted title again imports it again if the source still has it.

## Automatic metadata repair and country filtering

The web server starts a background metadata check after its first request. It scans existing movie and TV records for missing descriptions, years, genres, or posters, fetches incomplete detail pages one at a time, and retries unresolved titles after a day. The footer reports checked titles, fully completed records, and failed fetches; `/api/metadata-task` exposes the current status as JSON. No separate command is needed.

The Movies page Countries filter lists only countries recorded for local movies. Countries are read from a source detail page when that page supplies country fields. If the source does not supply a country, the title is not assigned one; the menu displays an empty state until real country data is available. Selected countries combine with year, genre, and sort filters.

For updates to an existing installation, extract `media-home-update.zip` over the project folder. It contains code and styles only, preserving `media.db`, `catalog.json`, cached images and ID lists.

## Content ratings and hidden titles

The Movies page Rating filter offers tickboxes for G, PG, PG-13, R and NC-17, plus any other certification values found in local movie records. It combines with genre, country, year and sort controls. Movies without a certification are excluded when a rating is selected.

Right-click a movie or show tile and choose **Hide Movie** or **Hide Show**. Hidden titles stay in `media.db` with a separate persistent hidden list. They are excluded from browsing, search, recommendations, metadata checks and direct detail/API pages, even if the catalog is refreshed. Use **Hidden** in the footer (or `/hidden`) to review the list and restore a title. The update ZIP excludes the database and cached artwork, preserving existing records and hidden choices.

## Profile menu

The profile icon opens History, Watch List and Watch Party links, followed by Login and Sign Up buttons. History records opened detail pages in this browser; Add to List on a movie or show detail page toggles the browser's local Watch List. These local lists are per browser and exclude titles hidden through the app. Watch Party and account authentication do not yet have a server implementation; their menu links open clear information pages instead of broken routes.

## Movie playback

The movie detail page's **Play** button opens an in-page player at `https://www.vidy.st/movie/<movie-id>?color=DC2626&autoplay=true`. Play requests browser fullscreen and otherwise fills the viewport. The close button and Escape key stop playback by unloading the frame. The toolbar also provides a direct link to the player if the external site does not allow embedding.

The movie player closes automatically when the embedded player reports an `ended` event to its parent page. Because `vidy.st` is a separate origin, browsers do not let this app inspect its video directly; automatic closing depends on the provider sending a completion message. The close button and Escape key always work. The original Movy logo link is removed from the page header.

Detail-page “You may like” recommendations display as portrait cover cards. The movie player fills the viewport and has a close button.

The homepage hero cycles through the five newest visible movies rated at least 7.0, using database backdrops and metadata. Ratings display to one decimal place.

The home hero slides are rendered from SQLite on each request; the captured fixed slides are removed from the template.

Homepage hero typography, metadata, certification, genres, and controls follow the movie detail hero. Play from the homepage opens the movie player.

The profile Sign Up and Login links open styled account forms. Accounts are saved in media.db with hashed passwords; set MOVY_SECRET_KEY for a fixed session secret or let the app create .signup-secret locally. Keep .signup-secret private.

Accounts collect a display name, email (used to sign in), and password. Existing account rows retain their usernames as display names during migration.

Movie filters now include an inclusive IMDb rating range and age Classification. Signed-in filter choices persist in the account across sessions. Hearts save favourites per account; view them from the profile menu.

Parental Controls in the signed-in profile menu saves hidden genres and age classifications to the account. Matching titles are excluded from browsing, search, favourites and recommendations, and direct detail URLs return 404 for that account.

Home tiles use database poster images, footer genre links stay on the local server, and the captured external hosting disclaimer is removed.

Scan Similar Titles checks every title visible to the signed-in account for newly linked movie/show IDs. It fetches candidate metadata to check genres and classification before inserting allowed IDs; the normal metadata worker completes the new records. The progress page reports checked titles, added IDs, and failures. Parental Controls are rechecked before each source and candidate fetch.

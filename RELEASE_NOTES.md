# Cyber Security News Powered by AI release notes

## Version 4.0.1 — See every collected article

### The problem

*Indexed articles* showed 63, but the list and *All topics* showed only 16. Nothing was missing: all 63 articles were stored and searchable. Two things hid most of them:

1. **After a manual refresh the list switches to only the articles that refresh added** (introduced in 3.4). The last refresh had added exactly 16, so the list and every topic count covered just those 16. The only way back was a small "Only the 16 new from the last refresh ×" pill, which was easy to miss.
2. **The default view is the last 7 days.** It held 60 of the 63 articles; the other 3 were published 8 days earlier and appear only under *All collected*. In addition, the list loaded 48 articles at a time, with the rest behind a *Load more* button.

### Fixes

- **A clear banner replaces the small pill.** Above the list it now says *Showing only the 16 new articles from your last refresh* and has a **Show all 63 collected articles** button, which clears every filter and switches to *All collected*.
- **Leaving the "new only" view now goes to All collected**, so every stored article is listed, instead of back to the 7-day window. Picking a time range also leaves that view.
- **The statistics at the top are clickable shortcuts that show exactly what they count:**
  - **Indexed articles** shows all collected articles with every filter cleared;
  - **Last 24 hours** and **Last 7 days** apply that time range;
  - **Active sources** opens *Manage sources*.
- **100 articles per page** instead of 48, so up to 100 are listed at once. When there are more, the button says exactly how many remain, for example *Show 100 more (137 not shown yet)*.

### Verified

On a copy of the real database (63 articles), a refresh that added 5 articles showed the banner with *All topics 5*. Clicking **Show all 63 collected articles** listed **63 of 63** (63 cards, *All topics 63*, chart total 63). The *Last 7 days* and *Last 24 hours* numbers led to 60 and 15 articles, matching their figures.

## Version 4.0 — Refresh log

Every refresh, whether **automatic** (the daily run) or **manual** (an analyst's *Refresh news*, for all, one or several sources), now appends one line to a plain-text log file, **`data/refresh.log`**. The file keeps a permanent record of when collection ran and how much new news it brought.

### The log file

- **Where:** `data/refresh.log`, in the same folder as the database. With Docker Compose that is the host's `./data` folder, so the log survives restarts, rebuilds and `docker compose down`, and can be read directly on the server (`cat data/refresh.log`, `tail -f data/refresh.log`, `grep automatic data/refresh.log`). Git ignores it. To store it elsewhere, set `PULSE_REFRESH_LOG`.
- **One line per refresh**, with fields separated by `|`:
  1. date and time it ran, in the server's time zone;
  2. kind: `automatic refresh`, `manual refresh (all enabled sources)` or `manual refresh (selected sources)`;
  3. result: `done` or `failed`;
  4. **number of new articles** brought in;
  5. duration;
  6. new articles **per source** (`failed` for a source that couldn't be reached);
  7. errors, or `none`.

  Example:
  `2026-09-28 02:00:04 -03 | automatic refresh | done | 12 new articles | 8.4 s | WIRED Security: 3, KrebsOnSecurity: 0, The Hacker News: 5, Dark Reading: 4, Google Threat Intelligence: 0, FortiGuard Labs: 0 | errors: none`
- **Runs that didn't complete normally are logged too:**
  - an automatic refresh *skipped* because no source is enabled;
  - an automatic refresh *postponed* because a manual refresh was running (with the retry time);
  - any refresh *interrupted* because the application was stopped while it was running.

  Each appears exactly once, never duplicated.
- **Persistent.** The log is never cleared: each refresh only *adds* a line. The file lives in the host folder `./data`, mounted into the container, so after `docker compose down` and `docker compose up` (or a reboot, or an image rebuild) the application still has every earlier line and keeps adding below them. Verified with the real sources: a container wrote its line, was removed completely, and a brand-new container showed that line in About and appended its own after it. The file stays owned by your user, not root.
- **Safe and tidy.** Line breaks and `|` characters inside error messages are neutralised, so each refresh stays on one line. The file rotates to `refresh.log.1` beyond 5 MB (decades of daily entries). If the log can't be written, a warning is shown but the refresh itself is never affected.

### In the application

- The About dialog has a new **Refresh log** section showing the latest 10 lines, newest first, and where the file is.
- The startup message shows the log location: `Refresh log (manual and automatic): data/refresh.log`.
- New read-only API endpoint `GET /api/refresh-log?lines=N` returns the latest entries.

### Verified

- With the six real sources, one automatic refresh and two manual refreshes (one source, then all sources) each wrote exactly one correct line: 2, 1 and 0 new articles respectively.
- Added 9 tests (85 in total), including one that restarts the logger on the same file and checks that earlier lines are kept and new ones appended.
- The tests also cover the line format, per-source counts and errors, skipped, postponed and interrupted runs, exactly one line per run (including when the app stops while a refresh is finishing), manual refreshes, rotation, an unwritable log not breaking a refresh, and the log API.

### Why a major version

The application now keeps a second persistent file next to the database, recording its activity; operations and backups should include it. Everything else stays compatible: databases, settings and the Docker setup from 3.x work unchanged.

## Version 3.5 — "News per day" chart

The main page has a new **News per day** chart showing how many stored articles were published on each date, split by topic.

### What it shows

- **Linked to the time filter.**
  - **24 hours:** one column per hour.
  - **7 / 15 / 30 days:** one column per day. The window rolls back from now, so it includes today so far plus the earlier part of the first day; for example, 15 days shows 16 columns.
  - **All collected:** one column per day from the first stored article, switching to one per week beyond 120 days.
- **Linked to the other filters too.** Topic, source, search and "new from the last refresh" narrow the chart the same way they narrow the list, so **the chart's total always equals the article count of the list**. A test checks this for every range and filter.
- **Columns stacked by topic**, with a **legend** giving each topic's count. Clicking a legend entry filters by that topic (the same as the topic chip); clicking it again shows all topics.
- **Easy to read.**
  - A subtitle states the total and the period, for example *30 articles published in the last 15 days, per day, by topic*.
  - The value is written on top of each column; when columns are too dense, only the busiest one is labelled.
  - Clean y-axis numbers and light gridlines; today's label is bold.
  - Hovering over a column, or moving across columns with the ← / → keys, shows a tooltip with the exact date, the total, and the count per topic.
  - **Show as table** switches to an accessible table with one row per day and a column per topic.
- Works in dark and light themes and on phones.

### Colours

- Each topic keeps its hue family (AI Security violet, Vulnerability red, Threat Intel amber, Supply Chain teal, SecOps blue), in stronger shades suited to chart fills. The card edges in the article list use the same colours, so a topic looks the same everywhere.
- The palette and the stacking order (Vulnerability, AI Security, Threat Intel, SecOps, Supply Chain, from the bottom up) were chosen by running a palette validator on both themes. No two touching colours are hard to tell apart, including for colour-blind readers (worst adjacent difference ΔE 19.5 in dark mode and 22.7 in light mode; 8 is the target). Two light-mode colours are below 3:1 contrast against white; the value labels and the table view cover that, as the method requires.

### Engineering

- New `static/chart.js`: a dependency-free SVG chart, with all text set through `textContent`.
- The articles API now also returns per-hour, per-topic counts computed with the same filters as the list, plus the start of the time window.
- Added 4 tests (76 in total). They cover chart totals matching the list for every range, per-hour/per-topic buckets, following the topic, source and search filters, and serving `chart.js`.

## Version 3.4 — Refresh brings only new news

A refresh, whether manual or automatic, adds only articles that have never been collected before, and after a manual refresh the page now shows exactly those articles.

### How "new" is decided

- An article counts as **new** when it isn't already stored: neither its link nor the same headline from the same source is in the database. Manual and automatic refreshes share the same database, so each one adds only what the previous refreshes didn't collect, whichever kind ran last.
- This is deliberately **not** "published after the last refresh time". Some publishers add items with an earlier date, or their feeds update late. A pure time cut-off would silently lose those articles; the "not stored yet" rule collects them once and never twice.
- Each source is still limited by its maximum number of articles and the look-back window from *Manage sources*.
- The time of every refresh is recorded (overall and per source) and shown on the main page, so each result can be compared with the previous refresh.

### Improvements

- **See only what the refresh brought.** When a manual refresh finishes, the list switches to **only the articles it added**, with a pill *Only the N new from the last refresh*. Remove the pill to go back to everything (the default 7-day view).
- **Clear result message.** The message after a refresh now compares with the previous one, for example *3 new articles since the last refresh (automatic, 8h ago) — showing only these*, or *No new articles since the last refresh (5 min ago): everything the sources published is already collected*. Per-source progress still shows *N new · M in window*.
- **Less wasted work.** Articles already stored under a different link (for example with `?utm_source=` tracking parameters) are now recognised by headline **before** summarising or downloading their page. Previously they were only rejected when saving: never duplicated, but processed for nothing.
- **Exact "new" tracking.** Every refresh gets a unique identifier, and the articles it adds are tagged with it, so the "new from the last refresh" view is exact even when two refreshes happen within the same second. Existing databases are upgraded automatically on first start; no action is needed.

### Verified

- On a copy of the live database with the six real sources, two refreshes in a row both reported *No new articles*, with 0 new for every source even though 30 items were in the window. After three Hacker News articles were removed from the copy, a Hacker News refresh brought back exactly those three and the list showed only them.
- 3 new tests (72 in total). They cover: a second refresh (automatic) adding only the two articles published in between, with no page downloads for known articles; the "new only" view returning exactly those two; a re-issued link with the same headline not counting as new; and a late-arriving article dated before the last refresh still being collected once.

## Version 3.3 — Automatic daily refresh and database size

News can now be refreshed in two ways:

1. **Manually, by the analyst:** *Refresh news* collects from all enabled sources, one source, or several chosen sources. This is unchanged.
2. **Automatically, by the application:** once a day, by default at **02:00**, it collects from every enabled source.

Before this version there was no automatic refresh; news was collected only when an analyst clicked *Refresh news*.

### Automatic daily refresh

- **On by default at 02:00, server local time.** It runs a full refresh of all **enabled** sources using the settings from *Manage sources*: the look-back window, and each source's own maximum number of articles. It's the same as clicking *Refresh all enabled sources*: only new articles are added, and they get summaries, topics and search indexing as usual.
- **Configurable.** *Manage sources* has a new **Automatic refresh** section with an on/off switch and the daily time (24-hour). Changing either never starts a refresh straight away; the next run is the next occurrence of the chosen time. Invalid times are refused.
- **A missed refresh is caught up.** If the application isn't running at the scheduled time (for example, the server was off overnight), the refresh runs once about 30 seconds after the next start. It runs only once, however many days were missed.
- **Never overlaps a manual refresh.** If an analyst's refresh is still running at the scheduled time, the automatic one waits and retries 5 minutes later.
- **Visible status.**
  - Under the statistics on the main page: *Automatic refresh daily at 02:00 (-03) · next tomorrow at 02:00 · last automatic run …*.
  - The *Last refresh* line says **(automatic)** when the latest refresh was the scheduled one.
  - The About dialog has a new **How news is refreshed** section.
  - Startup prints the next run, for example `Automatic refresh: daily at 02:00 (-03) — next run Mon 28 Sep 02:00`.
  - An open page notices a background refresh within a minute and reloads the news when it finishes.
- **Right time zone in Docker.** Containers default to UTC, which would have made "02:00" run at 23:00 in São Paulo. `compose.yaml` now mounts the host's `/etc/localtime` read-only, so the container uses the server's time zone (checked: `-03`).
- If the application is stopped during an automatic refresh, that run is recorded as *interrupted* and the next day's run goes ahead normally.

### Database size

- The About dialog's **Database size** now shows the size in the most suitable unit (bytes, KB, MB or GB), with the exact figure underneath in **bytes, MB and GB**, for example *88.0 KB — 90,112 bytes · 0.09 MB · 0.0001 GB*. It counts everything SQLite keeps on disk for the database (main file, write-ahead log and shared-memory file) and is updated each time About is opened.
- The startup message also shows the size, for example `Database: data/pulse.db (88.0 KB) — 30 articles kept …`.

### Fixes

- A stop request (`SIGTERM`) arriving during the first moments of startup is now handled cleanly too; the protection used to start only once the server was already running.

### Engineering

- New `pulse/scheduler.py` (`DailyScheduler`), with 10 tests (69 in total). They cover the first start, running once per day, only enabled sources, catch-up after downtime, settings changes, disabling, a manual refresh in progress, recording interrupted runs, and an end-to-end run that respects the per-source article limit.

## Version 3.2 — Explained statistics

### New features

- **"i" buttons on the statistics.** Each of the four figures at the top of the main page (**Last 24 hours**, **Last 7 days**, **Indexed articles**, **Active sources**) has an "i" button next to it that explains what the number means:
  - **Last 24 hours**: articles *published* in the past 24 hours, according to each source's own date, counted across all stored news. The filters below don't affect it.
  - **Last 7 days**: articles published in the past 7 days, which includes the last 24 hours, whatever filters are selected.
  - **Indexed articles**: every article saved and searchable, from all dates and all sources, including sources that were later disabled or removed. They are kept across restarts.
  - **Active sources**: enabled sources out of all configured sources. *Refresh all enabled sources* uses the enabled ones; a disabled source can still be refreshed on its own.
- The explanation appears when you hover over or keyboard-focus the button. Clicking or tapping keeps it open, and a second tap, Escape or clicking elsewhere closes it. Only one explanation is open at a time. On phones it spans the full width of the statistics block so it stays readable.
- Accessible: each button has a spoken label such as "What does Last 24 hours mean?", and the explanation is linked to it as its description.
- The About dialog's *What's new* list mentions this feature.

### Fixes

- The top (hero) section no longer clips content that extends past its bottom edge, so explanations for the lower row of statistics show in full.

## Version 3.1 — Persistent news across restarts, Docker and Python

Collected articles now stay available however the application is stopped and started: `docker compose down` / `up`, a server reboot, an image rebuild, or switching between Docker and `python3 server.py`. You don't need to collect again.

### What was wrong

- **Docker and Python used different databases.** Docker Compose kept its database in a private Docker volume, while `python3 server.py` used `./data/pulse.db`. Starting the other way showed an empty app, which looked as if the news had been lost.
- **`docker stop` / `docker compose down` force-killed the app after 10 seconds.** Python running as the container's main process (PID 1) ignored the stop signal. A refresh in progress was cut off, and the database was left without a clean shutdown.
- The README suggested `docker compose down -v`, which deletes a Docker volume and every article in it.

### Changes

- **One shared, persistent data folder.** `compose.yaml` now mounts the host folder `./data` (configurable with `PULSE_DATA_DIR`) instead of a Docker volume, so Docker and `python3 server.py` read and write the same `data/pulse.db`. The folder is on your disk, easy to find and back up, and it survives `docker compose down -v`.
- **Files stay yours.** The container runs as the owner of `./data` (`PULSE_UID` / `PULSE_GID`, default `1000:1000`), so the database is never owned by root or an unknown user.
- **Clean shutdown.** The app handles `SIGTERM` (Docker) and `Ctrl+C`. It stops accepting requests, lets a running refresh finish (up to 20 seconds; Compose allows 30 with `stop_grace_period`), and flushes SQLite's write-ahead log into `pulse.db` before exiting. In testing, stopping in the middle of a refresh took 2.7 seconds instead of a 10-second forced kill, and every article was kept.
- **You can see that the data is kept.**
  - On startup: `Database: data/pulse.db — 30 articles kept from previous runs (since 2026-09-27)`.
  - On shutdown: `Saved N articles — they will be there on the next start`.
  - The About dialog has a new **"Your data is kept"** panel with live figures: articles stored, collected since, database size and location.
- **Backups.** `python3 server.py --backup backup.db` writes a consistent copy even while the app is running. In Docker: `docker compose exec pulse python server.py --backup /app/data/backup.db`.
- **Clear permission error.** If the data folder isn't writable, the app stops at startup with the exact `chown` command to fix it, instead of failing later during a refresh.

### About dialog

- New **Running it** section: Docker Compose, Python with `--host 192.168.3.100`, the shared `data` folder, and the no-login warning for LAN access.
- New **What's new** section summarising versions 3.1, 3.0 and 2.0, with a link to these notes.
- The Claude AI card still shows whether enrichment is active and, if not, why.

### Upgrading from 3.0 with Docker

Articles collected by 3.0 **inside Docker** were in the volume `cyber-intel-pulse_pulse-data`. On this server that volume held 0 articles, while the 30 articles collected with `python3 server.py` are in `./data` and now appear in Docker too. After confirming the new setup, the old volume can be removed with `docker volume rm cyber-intel-pulse_pulse-data`.

### Engineering

- Added 4 tests (59 in total): articles, sources, settings and the last refresh survive a restart; the write-ahead-log flush and backup; the unwritable-folder error; and a real `server.py` process stopped with `SIGTERM` that exits in under 5 seconds with its data saved.
- `data/.gitkeep` keeps the data folder in the repository, so Docker never creates it as root. The database files stay git-ignored.

## Version 3.0 — LAN access, Docker deployment and easier Claude setup

Version 3.0 makes the application a network service. You can open it from other machines on your network through the server's interface address (for example `http://192.168.3.100:8000`) and run it as a Docker Compose service.

### New features

- **Open the app on your network address.**
  - Start it with `python3 server.py --host 192.168.3.100` to use the LAN interface, or `--host 0.0.0.0` for every interface. The default is still `127.0.0.1`, this machine only.
  - The address and port can also be set with `PULSE_HOST` / `PULSE_PORT`, or saved in `.env`.
  - On startup the app prints the exact URL to open.
  - If the address doesn't belong to the machine, or the port is taken, you get a clear message instead of a traceback.
- **Docker Compose deployment.** The new `Dockerfile` and `compose.yaml` run the app with `docker compose up -d --build`.
  - The image includes the Claude SDK, so AI enrichment only needs `ANTHROPIC_API_KEY` in `.env`.
  - The app is published only on `192.168.3.100:8000` by default; change this with `PULSE_BIND_IP` / `PULSE_PORT`.
  - Collected news is kept in the named volume `pulse-data` across rebuilds.
  - The container runs as an unprivileged user on a read-only filesystem, with no extra Linux capabilities, a health check and rotated logs.
- **`.env` settings file.** `server.py` reads a git-ignored `.env` file next to it for `ANTHROPIC_API_KEY`, `PULSE_MODEL`, `PULSE_AI`, `PULSE_HOST`, `PULSE_PORT`, `PULSE_ALLOWED_HOSTS` and `PULSE_DB`, so they don't have to be exported in every shell. Values that are already exported take priority. A commented `.env.example` is included. The file is never copied into the Docker image.
- **Claude setup that tells you what to do.** When Claude enrichment is off, the startup message now gives the reason and the fix:
  - *Package not installed*: Ubuntu 24.04 blocks system-wide `pip install` (PEP 668), so the message gives the virtual-environment commands, including the `python3-venv` package, or suggests the Docker image.
  - *No API key*: the message points to `.env`.
  - *Turned off*: the message says it was disabled with `PULSE_AI=0`.
- `PULSE_MODEL` is now read when the app starts, so a value set in `.env` takes effect.

### Security

- **Host-header protection works with LAN addresses.** The DNS-rebinding defence now compares only the hostname, so it keeps working behind Docker port mapping. It automatically accepts the address the server is bound to, plus loopback. Extra names (such as `pulse.lan`) can be added with `--allowed-host` or `PULSE_ALLOWED_HOSTS`. Requests addressed to any other name are rejected with a message explaining how to allow it.
- **The same-origin check for write requests also accepts allowed LAN addresses**, so refreshing and editing sources work from other machines, while requests from other websites are still refused.
- **LAN warning.** When the app listens on a non-loopback address, the startup message warns that there is **no login**: anyone who can reach the address can read the news, change sources and start refreshes. Keep it on a trusted network.

### Fixes

- Startup messages now appear immediately when output isn't a terminal (Docker, systemd, redirected logs). Previously they were held in Python's output buffer.

### Engineering

- Added 10 tests (55 in total) for `.env` parsing, each Claude status message, and Host/Origin checks with LAN IPs, wildcard binds and port mapping.
- Added `Dockerfile`, `compose.yaml`, `.dockerignore` and `.env.example`; `.env` is git-ignored.

## Version 2.0 — Searchable intelligence archive, Claude AI and a new interface

Version 2.0 is a rewrite of the collector, the storage layer and the interface, following a security and quality review of version 1.7.

### New features

- **Refresh exactly what you need.** The **Refresh news** menu collects from **all enabled sources**, from **one source only** (for example WIRED Security), or from **several chosen sources** in a dialog with Select all / Clear and a per-refresh look-back window. Refreshes run in the background with live progress for each source: found, new, errors, and whether a feed or Google News was used.
- **Every article has a summary.** Summaries come, in order of preference, from Claude, the publisher's feed abstract, or the article page's description. When none of these exist, a clearly labelled "Headline only" note is shown instead. Each card shows which kind of summary it has.
- **Full-text search.** Articles are stored in SQLite with an FTS5 index over title, summary, source and topic. Search supports word stems (*exploit* also finds *exploited*), `"exact phrases"`, CVE identifiers, `OR`, and source names. Matches are highlighted and results can be ordered by relevance or date.
- **Time filters.** Show news from the last **24 hours**, **7 days**, **15 days**, **30 days**, or **all collected** news.
- **Persistent archive.** Collected news is kept between reloads and restarts in `data/pulse.db`. Duplicates are removed by URL and headline.
- **Claude AI enrichment (optional).** With the `anthropic` package installed and `ANTHROPIC_API_KEY` set, Claude (`claude-opus-5` by default) writes article summaries, assigns topics, drops off-topic stories and drafts the daily briefing. Declined requests use the API's server-side fallback, and any failure falls back to the keyword pipeline.
- **Today's briefing.** The *Lead signal / Watch next / SOC action* panel is now generated from the latest collected news, by Claude or by a built-in heuristic, instead of being hard-coded.
- **Facets and trends.** Topic and source counts update with each search. The trending-terms cloud is computed over the whole result set, and selecting a term searches for it.
- **Source management on the server.** Enter a website, blog or feed URL. Feeds are discovered automatically and remembered, with Google News as a fallback. Per source you can set the article limit and an optional off-topic filter, and see the last check status.
- **Shareable views.** The search, time range, topic and source filters are kept in the page URL.
- "NEW" badges mark articles collected since your last visit.

### Interface

- New design: deep-navy console look with cyan-to-violet accents, glass panels, colour-coded topics, a statistics header, a sticky filter bar and a refined light theme. The theme follows the operating-system setting until you choose one.
- Whole-card links that are keyboard accessible, a `/` shortcut to search, a keyboard-navigable refresh menu, visible focus rings and reduced-motion support.
- Mobile layout: search stays visible, the source dialog stacks correctly, and there is no horizontal scrolling.

### Security fixes

- **Fixed cross-site scripting (XSS).** Feed titles, summaries and links were inserted as HTML. All content is now built with DOM text nodes, only `http`/`https` links are allowed, and a strict Content-Security-Policy is sent.
- **Fixed exposure of `.git/`, the source code and other files.** The server now serves only an explicit list of static files.
- **Hardened against server-side request forgery (SSRF).**
  - Only public `http`/`https` addresses on ports 80 and 443 are fetched.
  - Every redirect is checked again.
  - Hostnames must resolve only to public IP addresses, and the connection is pinned to the checked address, which blocks DNS rebinding.
  - Private and IPv6 ranges, `*.localhost`, `*.internal` and numeric IP tricks are blocked.
- **Blocked cross-site requests.** Write requests must be `application/json` and same-origin, and requests with an unexpected `Host` header are rejected.
- Invalid input now returns **HTTP 400** with a clear message instead of 502. Response sizes are capped.

### Fixes

- Feed URLs that you enter directly (such as `/feed.xml`) are now used instead of being ignored.
- Off-topic items (such as product reviews on WIRED) are no longer labelled Threat Intel. The defaults use WIRED's security feed, and an optional relevance filter is available per source.
- Topic classification uses whole-word, weighted matching, so "model year cars" or "prompted users" are no longer classified as AI Security.
- The trending cloud no longer shows filler words such as "the", "and" or "for".
- A broken CSS media query that made the source dialog unusable on phones has been fixed.
- Sources are collected in parallel. Refreshing the six default sources now takes about 3 seconds instead of about 22.
- The date and hero figures are no longer hard-coded to 28 August.

### Engineering

- The code is split into a `pulse/` package (fetching, feeds, text, classification, storage, collector, AI, web) and a `static/` front end.
- Added a 45-test `unittest` suite covering SSRF, parsing, search, collection, the HTTP API and the Claude integration (with a fake client).
- Added `.gitignore`, `CLAUDE.md` and `requirements-ai.txt`. The core application still needs only the Python standard library.

## Version 1.7 — Product identity and application details

- Renamed the application from **Signal//Noise** to **Cyber Security News Powered by AI**.
- Added an accessible **About** dialog describing the HTML, CSS, JavaScript, Python standard-library services, feeds, browser APIs, and typography used by the application.
- Added a direct link to the project’s GitHub repository.

## Version 1.6 — Streamlined source and trend controls

- Removed the **Signals in the reporting** label from the word-cloud panel to keep the trend view concise.
- Made **Select All** available in source management as well as focused-refresh selection.
- Added the current release version to the release-notes link in the application footer.

## Version 1.5 — Source-scoped briefing

- Applied saved source selections immediately to the intelligence queue, so only news from enabled sources is presented.
- Kept the word cloud aligned with the selected sources instead of deriving trends from hidden stories.
- Ensured focused and all-source refreshes display only the sources included in that refresh.

## Version 1.4 — Reliable focused refresh

- Fixed source-specific refreshes being reported as server failures when a reachable source had no posts inside the selected lookback window.
- Added explicit **Clear selection** and **Select all** controls to the focused-refresh dialog.
- Made focused selections temporary so a one-off scan does not overwrite saved source preferences.
- Added source names to scan progress and completion messages for clear analyst confirmation.
- Linked these release notes from the application footer.

## Version 1.3 — Focused collection and display themes

- Split **FRESH NEWS** into **FRESH ALL SOURCES** and **FRESH FROM SELECTED SOURCES** workflows.
- Added an explicit source-selection step for focused refreshes while preserving analyst source choices in the browser.
- Added a Dark/Light display toggle and persisted the selected theme between visits.
- Updated refresh progress and completion messages to identify whether all or selected sources were scanned.

## Version 1.2 — Dynamic intelligence discovery

- Added first-class collection from analyst-configured blog RSS/Atom feeds, with Google News search as a fallback.
- Ensured a newly saved, enabled source is sent with the very next **FRESH NEWS** request, including its article limit and the configured lookback window.
- Added a dynamic word cloud generated from the currently arrived intelligence set.
- Added click-to-filter behavior for cloud terms and expressions, plus a clear reset action.
- Added clearer source-refresh feedback so analysts can confirm the collection scope.

## Version 1.1 — Configurable fresh news

- Added live news refresh through the local Python service.
- Added source management for enabling, adding, removing, and resetting publications and blogs.
- Added configurable per-source article limits and a 1–30 day search window.
- Persisted collection settings in the browser.
- Added resilient article summaries, topic classification, refresh status, and accessible clickable news cards.

## Version 1.0 — Original scope

- Delivered the SIGNAL//NOISE cyber and AI intelligence briefing experience.
- Presented a curated queue of security news ranked for operational relevance.
- Organized reporting into AI Security, Vulnerability, Threat Intel, Supply Chain, and SecOps topics.
- Included topic filters, free-text search, source links, responsive cards, lead signals, and analyst-oriented summaries.
- Established the dark, high-signal editorial interface and practitioner-focused briefing format.

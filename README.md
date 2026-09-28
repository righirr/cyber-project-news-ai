# Cyber Security News Powered by AI

A local cyber-intelligence briefing. It collects recent reporting from the security blogs and news sites you choose, summarises and classifies every article, and keeps it all in a full-text index that you can search and filter by time, topic and source.

## Run with Python

```bash
cd <base_folder>/cyber-intel-pulse
python3 server.py --host 192.168.3.100
```

Then open http://192.168.3.100:8000. The `--host` value sets who can reach the app:

| `--host` | Who can open it |
|---|---|
| `127.0.0.1` (default) | this machine only (`http://localhost:8000`) |
| `192.168.3.100` | your LAN, through that interface |
| `0.0.0.0` | every interface |

Other options are `--port 8080`, `--db path/to/pulse.db`, `--allowed-host pulse.lan` (an extra name you type in the browser) and `--verbose`. Instead of typing these each time, copy `.env.example` to `.env` and set `PULSE_HOST=192.168.3.100` there.

> There is no login. Anyone who can reach the address can read the news, change sources and start refreshes, so keep it on a trusted network. If Ubuntu's firewall is on, allow the port with `sudo ufw allow from 192.168.3.0/24 to any port 8000 proto tcp`.

The core application needs **only the Python 3.10+ standard library**. News is stored in `data/pulse.db` (SQLite).

## Run with Docker Compose

```bash
cp .env.example .env      # set ANTHROPIC_API_KEY (optional) and PULSE_BIND_IP
docker compose up -d --build
docker compose logs -f    # shows the URL and the Claude status
```

The container is published on `PULSE_BIND_IP:PULSE_PORT` (default `192.168.3.100:8000`). The image already includes the Claude SDK. Useful commands:

- Update after code changes: `docker compose up -d --build`
- Stop: `docker compose down` (the news is kept, see below)
- Logs: `docker compose logs -f`

## Automatic daily refresh

Besides manual refreshes, the app collects from all **enabled** sources once a day, by default at **02:00** server time, using the look-back window and each source's article limit. Turn it on or off and change the time in **Manage sources → Automatic refresh**. If the app was off at that time, the missed refresh runs once after it starts. With Docker, the container uses the host time zone (`/etc/localtime` is mounted). The next run is shown under the statistics and in About.

## Your data is kept

Collected articles, sources and settings are stored in **`./data/pulse.db`** on the host. Docker Compose (through a bind mount) and `python3 server.py` use this same file, so you can stop, restart, reboot, rebuild the image or switch between Docker and Python without collecting again. Startup prints how many articles were kept, and the About dialog shows live figures.

- **Stopping is safe.** Ctrl+C or `docker compose down` lets a running refresh finish and flushes the database first.
- **Back up** by copying the `data` folder while the app is stopped, or at any time with `python3 server.py --backup backup.db` (Docker: `docker compose exec pulse python server.py --backup /app/data/backup.db`).
- **Deleting the news** means deleting `data/pulse.db` yourself. No Docker command removes it.
- The container runs as `PULSE_UID:PULSE_GID` (default `1000:1000`) so it can write `./data`. If your user ID differs (`id -u`), set both in `.env`.

## Enable Claude (optional)

Ubuntu 24.04 blocks system-wide `pip install`, so install the SDK in a virtual environment (or use Docker, which already includes it):

```bash
sudo apt install python3-venv
python3 -m venv .venv
.venv/bin/pip install -r requirements-ai.txt
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
.venv/bin/python server.py --host 192.168.3.100
```

The startup message shows `Claude enrichment: enabled (claude-opus-5)` when it's working, or explains what is missing.

With Claude enabled, each new article gets an AI-written summary, a topic and a relevance check, and the *Today's briefing* panel is written by Claude. The model is `claude-opus-5` by default; set `PULSE_MODEL` to change it. Set `PULSE_AI=1` to use an `ant auth login` profile instead of an API key, or `PULSE_AI=0` to turn Claude off. Without Claude, the app uses publisher abstracts, article-page descriptions and keyword classification.

## Using it

- **Refresh news** collects from all enabled sources, from **one source only**, or from **several chosen sources**, with a look-back window of 1–30 days. Progress is shown for each source.
- **Search** matches words (with stemming), `"exact phrases"`, CVE IDs, `OR`, and source names. Press `/` to jump to the search box.
- **Time filter** options are 24 hours, 7 days, 15 days, 30 days, or all collected news. You can also filter by topic and source. Every view has a shareable URL.
- **Manage sources** (the sliders icon): add any website, blog or RSS/Atom URL. Feeds are discovered automatically, with Google News used for sites without a feed. For general-news sites, turn on *Filter off-topic*.

## Development

```bash
python3 -m unittest discover -s tests -t .
```

Layout: `pulse/` holds the backend (`netguard` for SSRF-safe fetching, `feeds`, `text`, `classify`, `store` for SQLite/FTS5, `collector`, `ai`, `web`), `static/` holds the only files the server serves, and `tests/` holds the tests.

Source: [github.com/righirr/cyber-security-news-system](https://github.com/righirr/cyber-security-news-system) · [Release notes](RELEASE_NOTES.md)

#!/usr/bin/env python3
"""Start Cyber Security News Powered by AI.

Examples:
  python3 server.py                          # http://localhost:8000 (this machine only)
  python3 server.py --host 192.168.3.100     # reachable on the LAN at http://192.168.3.100:8000
  python3 server.py --host 0.0.0.0           # every interface

Settings can also come from environment variables or a .env file next to this script
(PULSE_HOST, PULSE_PORT, PULSE_ALLOWED_HOSTS, PULSE_DB, ANTHROPIC_API_KEY, PULSE_MODEL, PULSE_AI).
"""
import argparse
import ipaddress
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from pulse.envfile import load_env

ROOT = Path(__file__).resolve().parent


def _is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == 'localhost'


def main():
    sys.stdout.reconfigure(line_buffering=True)  # show startup lines immediately under Docker/systemd
    loaded = load_env(ROOT / '.env')  # before reading any setting below

    # Imported after .env is loaded so every module sees those settings.
    from pulse.ai import Claude
    from pulse.collector import Collector, Jobs
    from pulse.scheduler import DailyScheduler
    from pulse.store import Store
    from pulse.web import WILDCARD_HOSTS, App, make_server, primary_address

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default=os.environ.get('PULSE_HOST', '127.0.0.1'),
                        help='address to listen on (default 127.0.0.1; e.g. 192.168.3.100 or 0.0.0.0)')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PULSE_PORT') or os.environ.get('PORT') or 8000))
    parser.add_argument('--allowed-host', action='append', default=[], metavar='NAME',
                        help='extra hostname/IP browsers may use (repeatable; also PULSE_ALLOWED_HOSTS, comma-separated)')
    parser.add_argument('--db', default=os.environ.get('PULSE_DB', ROOT / 'data' / 'pulse.db'),
                        help='SQLite database path (default: data/pulse.db)')
    parser.add_argument('--backup', metavar='FILE',
                        help='write a consistent copy of the database to FILE and exit (safe while the app runs)')
    parser.add_argument('--verbose', action='store_true', help='log every request')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    allowed = args.allowed_host + [h for h in os.environ.get('PULSE_ALLOWED_HOSTS', '').split(',') if h.strip()]
    db_path = Path(args.db)
    ensure_writable(db_path)
    store = Store(db_path)
    if args.backup:
        store.backup(args.backup)
        print(f'Backup written to {args.backup} ({store.storage_info()["articles"]} articles).')
        return
    ai = Claude()
    storage_label = os.environ.get('PULSE_STORAGE_LABEL') or _display_path(db_path)
    jobs = Jobs(Collector(store, ai), store)
    scheduler = DailyScheduler(store, jobs)
    app = App(store, ai, jobs, storage_label=storage_label, scheduler=scheduler)
    try:
        server = make_server(app, host=args.host, port=args.port, allowed_hosts=allowed)
    except OSError as exc:
        raise SystemExit(f'Cannot listen on {args.host}:{args.port}: {exc.strerror}. '
                         'Check that the address belongs to this machine (ip -4 addr) and the port is free.')
    port = server.server_address[1]

    # `docker stop` / `docker compose down` send SIGTERM. Python running as PID 1 in a
    # container ignores it unless a handler exists, and Docker then SIGKILLs after 10 s.
    # Installed before anything else so a stop request at any moment shuts down cleanly.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        print('Cyber Security News Powered by AI')
        if loaded:
            print(f'  Settings loaded from .env: {", ".join(sorted(loaded))}')
        if args.host in WILDCARD_HOSTS:
            lan = primary_address()
            print(f'  Listening on all interfaces: http://localhost:{port}' + (f'  ·  http://{lan}:{port}' if lan else ''))
        else:
            shown = f'[{args.host}]' if ':' in args.host else args.host
            print(f'  Running at http://{shown}:{port}')
        if not _is_loopback(args.host):
            print('  Note: there is no login — anyone who can reach this address can read the news,\n'
                  '        change sources and start refreshes. Keep it on a trusted network.')
        info = store.storage_info()
        kept = (f'{info["articles"]} articles kept from previous runs (since {info["first_collected"][:10]})'
                if info['articles'] else 'empty — use Refresh news to collect articles')
        print(f'  Database: {storage_label} ({human_size(info["size_bytes"])}) — {kept}')
        scheduler.initialize()
        auto = scheduler.status()
        if auto['enabled']:
            upcoming = datetime.fromisoformat(auto['next_run'])
            print(f'  Automatic refresh: daily at {auto["time"]} ({auto["timezone"]}) — next run {upcoming:%a %d %b %H:%M}')
        else:
            print('  Automatic refresh: off (turn it on in Manage sources)')
        print(f'  Claude enrichment: {ai.reason}' + (f' ({ai.model})' if ai.enabled else ''))
        if ai.hint:
            print('  ' + ai.hint.replace('\n', '\n  '))
        scheduler.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        shutdown(server, app, store)


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def shutdown(server, app, store):
    """Stop cleanly: finish a running refresh, then flush the database to disk."""
    print('Shutting down…')
    server.server_close()
    if app.jobs.running():
        print('  Waiting up to 20 s for the running refresh to finish…')
        if not app.jobs.wait(20):
            print('  Refresh did not finish; articles saved so far are kept.')
    store.checkpoint()
    print(f'  Saved {store.storage_info()["articles"]} articles — they will be there on the next start.')


def human_size(size):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size} bytes' if unit == 'bytes' else f'{size:.1f} {unit}'
        size /= 1024


def _display_path(path):
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return path.name


def ensure_writable(db_path):
    """Fail early with a useful message when the data folder is not writable."""
    folder = db_path.parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass
    blocked = [p for p in (folder, db_path) if p.exists() and not os.access(p, os.W_OK)] or \
              ([] if folder.exists() else [folder])
    if blocked:
        uid, gid = os.getuid(), os.getgid()
        raise SystemExit(f'Cannot write to {blocked[0]} (running as uid {uid}). Collected news could not be saved.\n'
                         f'Fix the owner of the data folder, e.g.: sudo chown -R {uid}:{gid} data\n'
                         'With Docker Compose, set PULSE_UID/PULSE_GID in .env to the owner of ./data (id -u, id -g).')


if __name__ == '__main__':
    main()

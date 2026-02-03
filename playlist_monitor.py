"""Playlist monitoring system.

Manages a list of monitored playlists that are periodically checked
for new tracks and automatically synced to Navidrome.
"""

import json
import os
import sys
import threading
import time
import asyncio
import uuid
from datetime import datetime
from typing import Optional

MONITORED_PLAYLISTS_PATH = os.getenv(
    "TRACKDROP_MONITORED_PLAYLISTS_PATH",
    "/app/data/monitored_playlists.json",
)

_lock = threading.Lock()
_scheduler_thread: Optional[threading.Thread] = None
_scheduler_running = False


def _load_playlists() -> list:
    with _lock:
        if os.path.exists(MONITORED_PLAYLISTS_PATH):
            try:
                with open(MONITORED_PLAYLISTS_PATH, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return []


def _save_playlists(playlists: list):
    with _lock:
        os.makedirs(os.path.dirname(MONITORED_PLAYLISTS_PATH), exist_ok=True)
        with open(MONITORED_PLAYLISTS_PATH, "w") as f:
            json.dump(playlists, f, indent=2)


def get_monitored_playlists() -> list:
    """Return all monitored playlists."""
    return _load_playlists()


def add_monitored_playlist(
    url: str,
    name: str,
    platform: str,
    username: str,
    poll_interval_hours: int = 24,
) -> dict:
    """Add a playlist to be monitored. Returns the new entry."""
    playlists = _load_playlists()

    # Don't add duplicates
    for p in playlists:
        if p["url"] == url and p["username"] == username:
            return p

    entry = {
        "id": str(uuid.uuid4()),
        "url": url,
        "name": name,
        "platform": platform,
        "username": username,
        "poll_interval_hours": poll_interval_hours,
        "enabled": True,
        "added_at": datetime.now().isoformat(),
        "last_synced": None,
        "last_track_count": 0,
    }
    playlists.append(entry)
    _save_playlists(playlists)
    return entry


def update_monitored_playlist(playlist_id: str, updates: dict) -> Optional[dict]:
    """Update a monitored playlist's settings. Returns updated entry or None."""
    playlists = _load_playlists()
    for p in playlists:
        if p["id"] == playlist_id:
            for key in ("poll_interval_hours", "enabled", "name"):
                if key in updates:
                    p[key] = updates[key]
            _save_playlists(playlists)
            return p
    return None


def remove_monitored_playlist(playlist_id: str) -> bool:
    """Remove a playlist from monitoring. Returns True if found and removed."""
    playlists = _load_playlists()
    original_len = len(playlists)
    playlists = [p for p in playlists if p["id"] != playlist_id]
    if len(playlists) < original_len:
        _save_playlists(playlists)
        return True
    return False


def mark_synced(playlist_id: str, track_count: int = None):
    """Update last_synced (and optionally last_track_count) for a monitored playlist."""
    playlists = _load_playlists()
    for p in playlists:
        if p["id"] == playlist_id:
            p["last_synced"] = datetime.now().isoformat()
            if track_count is not None:
                p["last_track_count"] = track_count
            break
    _save_playlists(playlists)


def _sync_playlist(entry: dict, navidrome_api, update_status_fn):
    """Run a sync for a single monitored playlist."""
    from downloaders.playlist_downloader import download_playlist, extract_playlist_tracks

    download_id = str(uuid.uuid4())
    print(f"[PlaylistMonitor] Syncing: {entry['name']} ({entry['url']})")

    track_count = None
    try:
        # Get track count before downloading
        _, _, tracks = extract_playlist_tracks(entry["url"])
        if tracks:
            track_count = len(tracks)

        asyncio.run(
            download_playlist(
                url=entry["url"],
                username=entry["username"],
                navidrome_api=navidrome_api,
                download_id=download_id,
                update_status_fn=update_status_fn,
            )
        )
    except Exception as e:
        print(f"[PlaylistMonitor] Error syncing {entry['name']}: {e}", file=sys.stderr)

    mark_synced(entry["id"], track_count)


def _scheduler_loop(navidrome_api, update_status_fn, downloads_queue):
    """Background loop that checks monitored playlists and syncs when due."""
    global _scheduler_running
    _scheduler_running = True

    while _scheduler_running:
        try:
            playlists = _load_playlists()
            now = datetime.now()

            for entry in playlists:
                if not entry.get("enabled", True):
                    continue

                interval_hours = entry.get("poll_interval_hours", 24)
                last_synced = entry.get("last_synced")

                if last_synced:
                    try:
                        last_dt = datetime.fromisoformat(last_synced)
                        elapsed_hours = (now - last_dt).total_seconds() / 3600
                        if elapsed_hours < interval_hours:
                            continue
                    except (ValueError, TypeError):
                        pass

                # Time to sync — add to download queue and run
                download_id = str(uuid.uuid4())
                downloads_queue[download_id] = {
                    "id": download_id,
                    "username": entry.get("username", ""),
                    "artist": "Playlist Sync",
                    "title": entry["name"],
                    "status": "in_progress",
                    "start_time": datetime.now().isoformat(),
                    "message": "Auto-syncing monitored playlist...",
                    "current_track_count": 0,
                    "total_track_count": None,
                    "download_type": "playlist",
                    "tracks": [],
                    "downloaded_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                }

                _sync_playlist(entry, navidrome_api, update_status_fn)

        except Exception as e:
            print(f"[PlaylistMonitor] Scheduler error: {e}", file=sys.stderr)

        # Check every 5 minutes
        for _ in range(300):
            if not _scheduler_running:
                break
            time.sleep(1)


def start_scheduler(navidrome_api, update_status_fn, downloads_queue):
    """Start the background playlist monitoring scheduler."""
    global _scheduler_thread, _scheduler_running
    if _scheduler_thread and _scheduler_thread.is_alive():
        return

    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(navidrome_api, update_status_fn, downloads_queue),
        daemon=True,
    )
    _scheduler_thread.start()
    print("[PlaylistMonitor] Scheduler started.")


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler_running
    _scheduler_running = False

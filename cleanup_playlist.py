#!/usr/bin/env python3
"""CLI tool to clean up a downloaded playlist.

Reads a playlist download history JSON file, deletes songs that were
newly downloaded (not already in anyone's library), removes the
Navidrome playlist, and cleans up the history file.

Usage:
    python cleanup_playlist.py <history_file>
    python cleanup_playlist.py --list              # list all history files
    python cleanup_playlist.py --all               # clean up all playlists

Protected songs (rated >= 4 stars, starred, or in a user playlist) are kept.
"""

import argparse
import asyncio
import json
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ROOT_ND, USER_ND, PASSWORD_ND, MUSIC_LIBRARY_PATH,
    TARGET_COMMENT, LASTFM_TARGET_COMMENT, ALBUM_RECOMMENDATION_COMMENT,
    LISTENBRAINZ_ENABLED, LASTFM_ENABLED, LLM_TARGET_COMMENT, LLM_ENABLED,
    ADMIN_USER, ADMIN_PASSWORD, NAVIDROME_DB_PATH,
)
from apis.navidrome_api import NavidromeAPI
from utils import remove_empty_folders


PLAYLIST_HISTORY_DIR = os.getenv("TRACKDROP_PLAYLIST_HISTORY_DIR", "/app/data/playlist_history")


def get_navidrome_api():
    return NavidromeAPI(
        root_nd=ROOT_ND,
        user_nd=USER_ND,
        password_nd=PASSWORD_ND,
        music_library_path=MUSIC_LIBRARY_PATH,
        target_comment=TARGET_COMMENT,
        lastfm_target_comment=LASTFM_TARGET_COMMENT,
        album_recommendation_comment=ALBUM_RECOMMENDATION_COMMENT,
        listenbrainz_enabled=LISTENBRAINZ_ENABLED,
        lastfm_enabled=LASTFM_ENABLED,
        llm_target_comment=LLM_TARGET_COMMENT,
        llm_enabled=LLM_ENABLED,
        admin_user=ADMIN_USER,
        admin_password=ADMIN_PASSWORD,
        navidrome_db_path=NAVIDROME_DB_PATH,
    )


def list_history_files():
    """List all playlist history files."""
    if not os.path.exists(PLAYLIST_HISTORY_DIR):
        print("No playlist history directory found.")
        return []

    files = glob.glob(os.path.join(PLAYLIST_HISTORY_DIR, "*.json"))
    if not files:
        print("No playlist history files found.")
        return []

    print(f"\nPlaylist download history files ({len(files)}):\n")
    for f in sorted(files):
        try:
            with open(f, 'r') as fh:
                data = json.load(fh)
            name = data.get("playlist_name", "Unknown")
            user = data.get("username", "Unknown")
            track_count = len(data.get("tracks", []))
            created = data.get("created_at", "Unknown")
            print(f"  {os.path.basename(f)}")
            print(f"    Playlist: {name} (user: {user})")
            print(f"    Tracks downloaded: {track_count}")
            print(f"    Created: {created}")
            print()
        except Exception as e:
            print(f"  {os.path.basename(f)} - error reading: {e}")
    return files


def cleanup_playlist(history_path: str, navidrome_api: NavidromeAPI):
    """Clean up a single playlist from its history file."""
    if not os.path.exists(history_path):
        print(f"History file not found: {history_path}")
        return False

    with open(history_path, 'r') as f:
        history = json.load(f)

    playlist_name = history.get("playlist_name", "Unknown")
    tracks = history.get("tracks", [])

    print(f"\n{'='*60}")
    print(f"Cleaning up playlist: {playlist_name}")
    print(f"History file: {history_path}")
    print(f"Tracks to process: {len(tracks)}")
    print(f"{'='*60}")

    if not tracks:
        print("No tracks in history. Nothing to clean up.")
        _remove_navidrome_playlist(navidrome_api, playlist_name)
        os.remove(history_path)
        print(f"Removed empty history file: {history_path}")
        return True

    salt, token = navidrome_api._get_navidrome_auth_params()
    deleted = []
    kept = []
    failed = []
    remaining_tracks = []  # Tracks that couldn't be processed

    for track in tracks:
        artist = track.get("artist", "")
        title = track.get("title", "")
        nd_id = track.get("navidrome_id", "")
        file_path_rel = track.get("file_path", "")
        label = f"{artist} - {title}"

        if not nd_id:
            # Track was never downloaded successfully (no Deezer match).
            # No file exists to delete — just discard from history.
            print(f"  DISCARD (never downloaded, no navidrome_id): {label}")
            deleted.append(f"{label} (never downloaded)")
            continue

        # Check if song still exists in Navidrome
        song_details = navidrome_api._get_song_details(nd_id, salt, token)
        if not song_details:
            print(f"  ALREADY GONE: {label}")
            deleted.append(label)
            continue

        # Check protection
        protection = navidrome_api._check_song_protection(nd_id)
        if protection["protected"]:
            reasons = "; ".join(protection["reasons"])
            print(f"  KEEP (protected): {label} - {reasons}")
            kept.append(f"{label} ({reasons})")
            continue

        # Delete the file
        actual_path = navidrome_api._find_actual_song_path(file_path_rel, song_details)
        if actual_path and os.path.exists(actual_path):
            if navidrome_api._delete_song(actual_path):
                print(f"  DELETED: {label}")
                deleted.append(label)
            else:
                print(f"  FAILED to delete: {label}")
                failed.append(label)
                remaining_tracks.append(track)
        else:
            # Try searching by Navidrome's own path from the API
            nd_path = song_details.get("path", "")
            print(f"  FILE NOT FOUND on disk: {label}")
            print(f"    History path: {file_path_rel}")
            print(f"    Navidrome path: {nd_path}")
            print(f"    Song still exists in Navidrome (id={nd_id}) but file not found.")
            print(f"    Removing from history anyway (Navidrome will handle missing files on next scan).")
            deleted.append(f"{label} (file not found on disk)")

    # Remove the Navidrome playlist
    _remove_navidrome_playlist(navidrome_api, playlist_name)

    # Clean up empty folders
    remove_empty_folders(MUSIC_LIBRARY_PATH)

    # Trigger a library scan
    navidrome_api._start_scan()

    # Handle history file
    if remaining_tracks:
        # Some tracks failed — keep history with only the remaining ones
        history["tracks"] = remaining_tracks
        with open(history_path, 'w') as f:
            json.dump(history, f, indent=2)
        print(f"\nUpdated history file with {len(remaining_tracks)} remaining tracks: {history_path}")
    else:
        # All tracks processed — remove history file
        os.remove(history_path)
        print(f"\nRemoved history file: {history_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY for '{playlist_name}':")
    print(f"  Deleted/discarded: {len(deleted)}")
    print(f"  Kept (protected): {len(kept)}")
    print(f"  Failed (will retry next run): {len(failed)}")
    print(f"{'='*60}")

    return True


def _remove_navidrome_playlist(navidrome_api: NavidromeAPI, playlist_name: str):
    """Remove a Navidrome playlist by name (clear all songs)."""
    salt, token = navidrome_api._get_navidrome_auth_params()
    existing = navidrome_api._find_playlist_by_name(playlist_name, salt, token)
    if existing:
        navidrome_api._update_playlist(existing["id"], [], salt, token)
        print(f"  Cleared Navidrome playlist: {playlist_name}")
    else:
        print(f"  Navidrome playlist not found: {playlist_name}")


def main():
    parser = argparse.ArgumentParser(description="Clean up downloaded playlists")
    parser.add_argument("history_file", nargs="?", help="Path to playlist history JSON file")
    parser.add_argument("--list", action="store_true", help="List all playlist history files")
    parser.add_argument("--all", action="store_true", help="Clean up all playlists")
    args = parser.parse_args()

    if args.list:
        list_history_files()
        return

    navidrome_api = get_navidrome_api()

    if args.all:
        files = glob.glob(os.path.join(PLAYLIST_HISTORY_DIR, "*.json"))
        if not files:
            print("No playlist history files found.")
            return
        for f in files:
            cleanup_playlist(f, navidrome_api)
        return

    if not args.history_file:
        parser.print_help()
        print(f"\nTip: Use --list to see available history files in {PLAYLIST_HISTORY_DIR}")
        return

    cleanup_playlist(args.history_file, navidrome_api)


if __name__ == "__main__":
    main()

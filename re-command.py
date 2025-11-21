#!/usr/bin/env python3

import asyncio
import os
import sys
import argparse
from tqdm import tqdm

from config import *
from apis.deezer_api import DeezerAPI
from apis.lastfm_api import LastFmAPI
from apis.listenbrainz_api import ListenBrainzAPI
from apis.navidrome_api import NavidromeAPI
from downloaders.track_downloader import TrackDownloader
from downloaders.album_downloader import AlbumDownloader
from utils import remove_empty_folders, Tagger

async def process_recommendations(source="all", bypass_playlist_check=False):
    """
    Processes recommendations from specified sources (ListenBrainz, Last.fm, or all).
    """
    print(f"Starting re-command script for source: {source}...")

    tagger = Tagger()
    deezer_api = DeezerAPI()
    lastfm_api = LastFmAPI()
    listenbrainz_api = ListenBrainzAPI()
    navidrome_api = NavidromeAPI()
    track_downloader = TrackDownloader(tagger)

    all_recommendations = []

    if source in ["all", "listenbrainz"] and LISTENBRAINZ_ENABLED:
        print("\nChecking for new ListenBrainz recommendations...")
        if bypass_playlist_check or await listenbrainz_api.has_playlist_changed():
            lb_recs = await listenbrainz_api.get_listenbrainz_recommendations()
            if lb_recs:
                print(f"Found {len(lb_recs)} new ListenBrainz recommendations.")
                for song in lb_recs:
                    print(f"- {song['artist']} - {song['title']} from album {song['album']}")
                all_recommendations.extend(lb_recs)
            else:
                print("No new ListenBrainz recommendations found.")
        else:
            print("ListenBrainz playlist has not changed. Skipping ListenBrainz recommendations.")
    elif source == "listenbrainz":
        print("ListenBrainz is not enabled. Skipping ListenBrainz recommendations.")

    if source in ["all", "lastfm"] and LASTFM_ENABLED:
        print("\nChecking for new Last.fm recommendations...")
        lf_recs = lastfm_api.get_lastfm_recommendations()
        if lf_recs:
            print(f"Found {len(lf_recs)} new Last.fm recommendations.")
            for song in lf_recs:
                print(f"- {song['artist']} - {song['title']} from album {song['album']}")
            all_recommendations.extend(lf_recs)
        else:
            print("No new Last.fm recommendations found.")
    elif source == "lastfm":
        print("Last.fm is not enabled. Skipping Last.fm recommendations.")

    # Remove duplicates based on artist and title
    unique_recommendations = []
    seen_tracks = set()
    for rec in all_recommendations:
        track_identifier = (rec['artist'], rec['title'])
        if track_identifier not in seen_tracks:
            unique_recommendations.append(rec)
            seen_tracks.add(track_identifier)

    if unique_recommendations:
        downloaded_songs_info = []
        for song_info in tqdm(unique_recommendations, desc="Downloading Recommendations", unit="song"):
            print(f"Processing: {song_info['artist']} - {song_info['title']} (Source: {song_info['source']})")
            try:
                downloaded_file_path = await track_downloader.download_track(song_info)
                if downloaded_file_path:
                    downloaded_songs_info.append(song_info)
                else:
                    print(f"Skipping download for {song_info['artist']} - {song_info['title']} (download failed).")
            except Exception as e:
                print(f"Error processing {song_info['artist']} - {song_info['title']}: {e}")

        if downloaded_songs_info:
            print("\nSuccessfully downloaded and tagged the following songs:")
            for song in downloaded_songs_info:
                print(f"- {song['artist']} - {song['title']} (Source: {song['source']})")

            # Organize the newly downloaded and tagged files
            navidrome_api.organize_music_files(
                TEMP_DOWNLOAD_FOLDER,
                MUSIC_LIBRARY_PATH
            )
        else:
            print("\nNo new songs were downloaded.")
    else:
        print("\nNo new recommendations found from ListenBrainz or Last.fm.")

    print("Script finished.")

async def process_fresh_releases_albums():
    """
    Downloads albums from Fresh Releases.
    """
    print("Starting re-command script for fresh releases albums...")

    tagger = Tagger()
    listenbrainz_api = ListenBrainzAPI()
    navidrome_api = NavidromeAPI()
    album_downloader = AlbumDownloader(tagger)

    if not LISTENBRAINZ_ENABLED:
        print("ListenBrainz is not enabled. Cannot fetch fresh releases.")
        return

    print("\nFetching fresh releases from ListenBrainz...")
    fresh_releases_data = await listenbrainz_api.get_fresh_releases()
    releases = fresh_releases_data.get('payload', {}).get('releases', [])

    if not releases:
        print("No fresh releases found.")
        return

    print(f"Found {len(releases)} fresh releases.")
    for release in releases:
        artist = release.get('artist_credit_name', 'Unknown Artist')
        album = release.get('release_name', 'Unknown Album')
        date = release.get('release_date', 'Unknown Date')
        print(f"- {artist} - {album} ({date})")

    downloaded_albums_info = []
    for release in tqdm(releases, desc="Downloading Fresh Releases Albums", unit="album"):
        artist = release.get('artist_credit_name', 'Unknown Artist')
        album = release.get('release_name', 'Unknown Album')
        release_date = release.get('release_date')
        album_art = release.get('album_art')

        album_info = {
            'artist': artist,
            'album': album,
            'release_date': release_date,
            'album_art': album_art
        }

        print(f"Processing album: {artist} - {album}")
        try:
            downloaded_files = await album_downloader.download_album(album_info)
            if downloaded_files:
                downloaded_albums_info.append(album_info)
                print(f"Successfully downloaded album: {artist} - {album}")
            else:
                print(f"Skipping download for album {artist} - {album} (download failed).")
        except Exception as e:
            print(f"Error processing album {artist} - {album}: {e}")

    if downloaded_albums_info:
        print("\nSuccessfully downloaded and tagged the following albums:")
        for album_info in downloaded_albums_info:
            print(f"- {album_info['artist']} - {album_info['album']}")

        # Organize the newly downloaded and tagged files
        navidrome_api.organize_music_files(
            TEMP_DOWNLOAD_FOLDER,
            MUSIC_LIBRARY_PATH
        )
    else:
        print("\nNo new albums were downloaded.")

    print("Script finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-command Recommendation Script.")
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["all", "listenbrainz", "lastfm", "fresh_releases"],
        help="Specify the source for recommendations (all, listenbrainz, lastfm) or 'fresh_releases' to download albums from fresh releases."
    )
    parser.add_argument(
        "--bypass-playlist-check",
        action="store_true",
        help="Bypass playlist change verification for ListenBrainz (always download recommendations)."
    )
    args = parser.parse_args()

    if args.source == "fresh_releases":
        asyncio.run(process_fresh_releases_albums())
    else:
        asyncio.run(process_recommendations(source=args.source, bypass_playlist_check=args.bypass_playlist_check))

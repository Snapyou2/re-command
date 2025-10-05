#!/usr/bin/env python3

import asyncio
import os
import sys
from tqdm import tqdm

from config_manager import ConfigManager
from apis.deezer_api import DeezerAPI
from apis.lastfm_api import LastFmAPI
from apis.listenbrainz_api import ListenBrainzAPI
from apis.navidrome_api import NavidromeAPI
from downloaders.track_downloader import TrackDownloader
from utils import remove_empty_folders, Tagger

async def main():
    """Main function to run the Navidrome recommendation script."""

    print("Starting weekly re-command script...")

    config_manager = ConfigManager()
    tagger = Tagger(config_manager)
    deezer_api = DeezerAPI()
    lastfm_api = LastFmAPI(config_manager)
    listenbrainz_api = ListenBrainzAPI(config_manager)
    navidrome_api = NavidromeAPI(config_manager)
    track_downloader = TrackDownloader(config_manager, tagger)

    # Parse Navidrome library and provide feedback to ListenBrainz (Steps 1 & 3)
    navidrome_api.process_navidrome_library(listenbrainz_api)

    # Clean up any empty directories in your music library (Step 2)
    remove_empty_folders(config_manager.get("MUSIC_LIBRARY_PATH"))

    all_recommendations = []

    # Get ListenBrainz recommendations
    if config_manager.get("LISTENBRAINZ_ENABLED"):
        if listenbrainz_api.has_playlist_changed():
            lb_recs = listenbrainz_api.get_listenbrainz_recommendations()
            if lb_recs:
                print(f"Found {len(lb_recs)} new ListenBrainz recommendations.")
                for song in lb_recs:
                    print(f"- {song['artist']} - {song['title']} from album {song['album']}")
                all_recommendations.extend(lb_recs)
            else:
                print("No new ListenBrainz recommendations found.")
        else:
            print("ListenBrainz playlist has not changed. Skipping ListenBrainz recommendations.")

    # Get Last.fm recommendations
    if config_manager.get("LASTFM_ENABLED"):
        lf_recs = lastfm_api.get_lastfm_recommendations()
        if lf_recs:
            print(f"Found {len(lf_recs)} new Last.fm recommendations.")
            for song in lf_recs:
                print(f"- {song['artist']} - {song['title']} from album {song['album']}")
            all_recommendations.extend(lf_recs)
        else:
            print("No new Last.fm recommendations found.")
    else:
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
                config_manager.get("TEMP_DOWNLOAD_FOLDER"),
                config_manager.get("MUSIC_LIBRARY_PATH")
            )
        else:
            print("\nNo new songs were downloaded.")
    else:
        print("\nNo new recommendations found from ListenBrainz or Last.fm.")

    print("Script finished.")

if __name__ == "__main__":
    asyncio.run(main())

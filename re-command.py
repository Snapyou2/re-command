#!/usr/bin/env python3

import asyncio
import os
import sys
import argparse
from tqdm import tqdm

from config import *
from apis.deezer_api import DeezerAPI
from apis.lastfm_api import LastFmAPI
from utils import initialize_streamrip_db, update_status_file # Import the new initialization function and update_status_file
from apis.listenbrainz_api import ListenBrainzAPI
from apis.navidrome_api import NavidromeAPI
from apis.llm_api import LlmAPI
from downloaders.track_downloader import TrackDownloader
from downloaders.album_downloader import AlbumDownloader
from utils import remove_empty_folders, Tagger

async def process_navidrome_cleanup():
    """
    Processes Navidrome library for cleanup based on ratings and submits feedback.
    """
    print("Starting Navidrome cleanup and feedback submission...")

    listenbrainz_api = ListenBrainzAPI(
        root_lb=ROOT_LB,
        token_lb=TOKEN_LB,
        user_lb=USER_LB,
        listenbrainz_enabled=LISTENBRAINZ_ENABLED
    )
    lastfm_api = LastFmAPI(
        api_key=LASTFM_API_KEY,
        api_secret=LASTFM_API_SECRET,
        username=LASTFM_USERNAME,
        session_key=LASTFM_SESSION_KEY,
        lastfm_enabled=LASTFM_ENABLED
    )
    navidrome_api = NavidromeAPI(
        root_nd=ROOT_ND,
        user_nd=USER_ND,
        password_nd=PASSWORD_ND,
        music_library_path=MUSIC_LIBRARY_PATH,
        target_comment=TARGET_COMMENT,
        lastfm_target_comment=LASTFM_TARGET_COMMENT
    )
    
    await navidrome_api.process_navidrome_library(
        listenbrainz_api=listenbrainz_api,
        lastfm_api=lastfm_api
    )

    print("Navidrome cleanup and feedback submission finished.")


async def process_recommendations(source="all", bypass_playlist_check=False, download_id=None):
    """
    Processes recommendations from specified sources (ListenBrainz, Last.fm, or all).
    """
    print(f"Starting re-command script for source: {source}...")
    # Clear debug log
    try:
        with open('/app/debug.log', 'w') as f:
            f.write(f"Starting re-command script for source: {source}\n")
    except:
        pass

    tagger = Tagger()
    deezer_api = DeezerAPI()
    lastfm_api = LastFmAPI(
        api_key=LASTFM_API_KEY,
        api_secret=LASTFM_API_SECRET,
        username=LASTFM_USERNAME,
        session_key=LASTFM_SESSION_KEY,
        lastfm_enabled=LASTFM_ENABLED
    )
    listenbrainz_api = ListenBrainzAPI(
        root_lb=ROOT_LB,
        token_lb=TOKEN_LB,
        user_lb=USER_LB,
        listenbrainz_enabled=LISTENBRAINZ_ENABLED
    )
    navidrome_api = NavidromeAPI(
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
        llm_enabled=LLM_ENABLED
    )
    track_downloader = TrackDownloader(tagger)

    all_recommendations = []

    if source in ["all", "listenbrainz"] and LISTENBRAINZ_ENABLED:
        print("\033[1;34m=== LISTENBRAINZ RECOMMENDATIONS ===\033[0m")
        print("\nChecking for new ListenBrainz recommendations...")
        if bypass_playlist_check or await listenbrainz_api.has_playlist_changed():
            lb_recs = await listenbrainz_api.get_listenbrainz_recommendations()
            if lb_recs:
                print(f"Found {len(lb_recs)} new ListenBrainz recommendations.")
                for song in lb_recs:
                    print(f"  - {song['artist']} - {song['title']} ({song['album']})")
                all_recommendations.extend(lb_recs)
            else:
                print("No new ListenBrainz recommendations found.")
        else:
            print("ListenBrainz playlist has not changed. Skipping ListenBrainz recommendations.")
    elif source == "listenbrainz":
        print("ListenBrainz is not enabled. Skipping ListenBrainz recommendations.")

    if source in ["all", "lastfm"] and LASTFM_ENABLED:
        print("\033[1;31m=== LAST.FM RECOMMENDATIONS ===\033[0m")
        print("\nChecking for new Last.fm recommendations...")
        lf_recs = await lastfm_api.get_lastfm_recommendations()
        if lf_recs:
            print(f"Found {len(lf_recs)} new Last.fm recommendations.")
            for song in lf_recs:
                print(f"  - {song['artist']} - {song['title']} ({song['album']})")
            all_recommendations.extend(lf_recs)
        else:
            print("No new Last.fm recommendations found.")
    elif source == "lastfm":
        print("Last.fm is not enabled. Skipping Last.fm recommendations.")

    if source in ["all", "llm"] and LLM_ENABLED and LLM_API_KEY:
        print("\033[1;32m=== LLM RECOMMENDATIONS ===\033[0m")
        print("\nGenerating LLM recommendations...")
        try:
            # Create LLM API instance only when needed
            llm_api = LlmAPI(
                provider=LLM_PROVIDER,
                gemini_api_key=LLM_API_KEY if LLM_PROVIDER == 'gemini' else None,
                openrouter_api_key=LLM_API_KEY if LLM_PROVIDER == 'openrouter' else None,
                model_name=globals().get('LLM_MODEL_NAME')
            )
            # Get weekly scrobbles for LLM
            scrobbles = await listenbrainz_api.get_weekly_scrobbles()
            if scrobbles:
                llm_recs = llm_api.get_recommendations(scrobbles)
                if llm_recs:
                    print(f"Found {len(llm_recs)} new LLM recommendations.")
                    # Process LLM recommendations to add required metadata fields
                    processed_llm_recs = []
                    for rec in llm_recs:
                        # Add required fields for download processing
                        rec['recording_mbid'] = ''  # Not available from LLM
                        rec['release_date'] = ''  # Not available from LLM
                        rec['caa_release_mbid'] = None
                        rec['caa_id'] = None
                        rec['source'] = 'LLM'

                        processed_llm_recs.append(rec)
                        print(f"  - {rec['artist']} - {rec['title']} ({rec['album']})")

                    all_recommendations.extend(processed_llm_recs)
                else:
                    print("LLM failed to generate recommendations.")
            else:
                print("No recent scrobbles found for LLM recommendations.")
        except Exception as e:
            print(f"Error getting LLM recommendations: {e}")
    elif source == "llm":
        if not LLM_ENABLED:
            print("LLM is not enabled. Skipping LLM recommendations.")
        elif not LLM_API_KEY:
            print("LLM API key is not configured. Skipping LLM recommendations.")

    # Remove duplicates based on artist and title
    unique_recommendations = []
    seen_tracks = set()
    for rec in all_recommendations:
        track_identifier = (rec['artist'], rec['title'])
        if track_identifier not in seen_tracks:
            unique_recommendations.append(rec)
            seen_tracks.add(track_identifier)

    if unique_recommendations:
        print("\033[1;33m=== DOWNLOADING TRACKS ===\033[0m")
        total = len(unique_recommendations)
        source_name = "ListenBrainz" if "listenbrainz" in source.lower() else "Last.fm"
        title = f"Downloading {source_name} Playlist"
        update_status_file(download_id, "in_progress", f"Starting download of {total} tracks.", title, current_track_count=0, total_track_count=total)
        downloaded_songs_info = []
        with tqdm(unique_recommendations, desc="Downloading Recommendations", unit="song") as pbar:
            for i, song_info in enumerate(pbar):
                tqdm.write(f"Processing: {song_info['artist']} - {song_info['title']} (Source: {song_info['source']})")
                try:
                    # Determine if this is a ListenBrainz recommendation
                    lb_recommendation = song_info.get('source', '').lower() == 'listenbrainz'
                    downloaded_file_path = await track_downloader.download_track(song_info, lb_recommendation=lb_recommendation)
                    if downloaded_file_path:
                        downloaded_songs_info.append(song_info)
                        # Update progress
                        update_status_file(download_id, "in_progress", f"Downloaded {len(downloaded_songs_info)} of {total} tracks.", title, current_track_count=len(downloaded_songs_info), total_track_count=total)
                    else:
                        tqdm.write(f"Skipping download for {song_info['artist']} - {song_info['title']} (download failed).")
                except Exception as e:
                    tqdm.write(f"Error processing {song_info['artist']} - {song_info['title']}: {e}")

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
        print("\nNo new recommendations found from enabled sources.")

    print("Script finished.")
    downloaded_count = len(downloaded_songs_info) if 'downloaded_songs_info' in locals() else 0
    total_count = len(unique_recommendations)
    message = f"Downloaded {downloaded_count} of {total_count} tracks."
    title = "Download Complete"
    update_status_file(download_id, "completed", message, title, current_track_count=downloaded_count, total_track_count=total_count)
    return downloaded_count, total_count

async def process_fresh_releases_albums(download_id=None):
    """
    Downloads albums from Fresh Releases.
    """
    print("Starting re-command script for fresh releases albums...")

    tagger = Tagger()
    listenbrainz_api = ListenBrainzAPI(
        root_lb=ROOT_LB,
        token_lb=TOKEN_LB,
        user_lb=USER_LB,
        listenbrainz_enabled=LISTENBRAINZ_ENABLED
    )
    navidrome_api = NavidromeAPI(
        root_nd=ROOT_ND,
        user_nd=USER_ND,
        password_nd=PASSWORD_ND,
        music_library_path=MUSIC_LIBRARY_PATH,
        target_comment=TARGET_COMMENT,
        lastfm_target_comment=LASTFM_TARGET_COMMENT,
        album_recommendation_comment=ALBUM_RECOMMENDATION_COMMENT,
        listenbrainz_enabled=LISTENBRAINZ_ENABLED,
        lastfm_enabled=LASTFM_ENABLED
    )
    album_downloader = AlbumDownloader(tagger)

    if not LISTENBRAINZ_ENABLED:
        print("ListenBrainz is not enabled. Cannot fetch fresh releases.")
        return

    print("\nFetching fresh releases from ListenBrainz...")
    fresh_releases_data = await listenbrainz_api.get_fresh_releases()
    releases = fresh_releases_data.get('payload', {}).get('releases', [])

    if not releases:
        print("No fresh releases found.")
        update_status_file(download_id, "completed", "No fresh releases found.", "No Fresh Releases", current_track_count=0, total_track_count=0)
        return

    print(f"Found {len(releases)} fresh releases.")
    for release in releases:
        artist = release.get('artist_credit_name', 'Unknown Artist')
        album = release.get('release_name', 'Unknown Album')
        date = release.get('release_date', 'Unknown Date')
        print(f"- {artist} - {album} ({date})")

    total_albums = len(releases)
    update_status_file(download_id, "in_progress", f"Starting download of {total_albums} albums.", "Downloading Fresh Releases Albums", current_track_count=0, total_track_count=total_albums)
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
                update_status_file(download_id, "in_progress", f"Downloaded {len(downloaded_albums_info)} of {total_albums} albums.", "Downloading Fresh Releases Albums", current_track_count=len(downloaded_albums_info), total_track_count=total_albums)
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
    downloaded_count = len(downloaded_albums_info)
    message = f"Downloaded {downloaded_count} of {total_albums} albums."
    title = "Download Complete"
    update_status_file(download_id, "completed", message, title, current_track_count=downloaded_count, total_track_count=total_albums)

if __name__ == "__main__":
    # Initialize streamrip database at the very start
    initialize_streamrip_db()

    parser = argparse.ArgumentParser(description="Re-command Recommendation Script.")
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["all", "listenbrainz", "lastfm", "llm", "fresh_releases"],
        help="Specify the source for recommendations (all, listenbrainz, lastfm, llm) or 'fresh_releases' to download albums from fresh releases."
    )
    parser.add_argument(
        "--bypass-playlist-check",
        action="store_true",
        help="Bypass playlist change verification for ListenBrainz (always download recommendations)."
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Process Navidrome library for cleanup based on ratings and submit feedback."
    )
    parser.add_argument(
        "--download-id",
        type=str,
        help="Unique ID for the download task, used for status tracking."
    )
    args = parser.parse_args()

    # Initial status update
    update_status_file(args.download_id, "in_progress", "Download initiated.")

    try:
        if args.source == "fresh_releases":
            asyncio.run(process_fresh_releases_albums(download_id=args.download_id))
        elif args.cleanup:
            asyncio.run(process_navidrome_cleanup())
            update_status_file(args.download_id, "completed", "Cleanup finished successfully.", "Cleanup completed")
        else:
            asyncio.run(process_recommendations(source=args.source, bypass_playlist_check=args.bypass_playlist_check, download_id=args.download_id))
    except Exception as e:
        update_status_file(args.download_id, "failed", f"Download failed: {e}", f"Download failed: {e}")
        raise # Re-raise the exception after updating status

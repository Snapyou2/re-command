import os
import asyncio
import re
import sys
import requests
import json # For pretty printing JSON in debug
from streamrip.client import DeezerClient
from streamrip.media import PendingSingle, PendingAlbum, PendingPlaylist
from streamrip.config import Config
from streamrip.db import Database, Downloads, Failed, Dummy
from config import *
from utils import Tagger, sanitize_filename, update_status_file
from apis.navidrome_api import NavidromeAPI
from downloaders.track_downloader import TrackDownloader
from typing import Optional

class LinkDownloader:
    def __init__(self, tagger, navidrome_api, deezer_api):
        self.tagger = tagger
        self.navidrome_api = navidrome_api
        self.deezer_api = deezer_api
        self.temp_download_folder = TEMP_DOWNLOAD_FOLDER
        self.music_library_path = MUSIC_LIBRARY_PATH
        self.track_downloader = TrackDownloader(tagger)
        self.streamrip_config = Config("/root/.config/streamrip/config.toml")
        self.deezer_client = DeezerClient(config=self.streamrip_config)
        # Use Dummy DB so streamrip never skips previously-seen tracks
        self.rip_db = Database(downloads=Dummy(), failed=Dummy())
        self.songlink_base_url = "https://api.song.link/v1-alpha.1"

    async def download_from_url(self, url: str, lb_recommendation: bool = False, download_id: Optional[str] = None):
        # Regex for supported platforms
        spotify_track_re = r"open\.spotify\.com\/track\/([a-zA-Z0-9]+)"
        spotify_album_re = r"open\.spotify\.com\/album\/([a-zA-Z0-9]+)"
        spotify_playlist_re = r"open\.spotify\.com\/playlist\/([a-zA-Z0-9]+)"
        youtube_playlist_re = r"(?:music\.youtube\.com|youtube\.com)\/playlist\?list=([a-zA-Z0-9_-]+)"
        youtube_re = r"(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/|clip\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})"
        deezer_track_re = r"deezer\.com(?:\/[a-z]{2})?\/track\/(\d+)"
        deezer_album_re = r"deezer\.com(?:\/[a-z]{2})?\/album\/(\d+)"
        deezer_playlist_re = r"deezer\.com(?:\/[a-z]{2})?\/playlist\/(\d+)"
        deezer_short_re = r"link\.deezer\.com\/s\/([a-zA-Z0-9]+)"
        apple_track_re = r"music\.apple\.com\/(?:[a-z]{2}\/)?song\/[^\/]+\/(\d+)"
        apple_album_re = r"music\.apple\.com\/(?:[a-z]{2}\/)?album\/[^\/]+\/(\d+)"
        tidal_track_re = r"tidal\.com\/track\/(\d+)"
        tidal_album_re = r"tidal\.com\/album\/(\d+)"
        amazon_track_re = r"music\.amazon\.[a-z]{2,3}\/tracks\/([A-Z0-9]+)"
        amazon_album_re = r"music\.amazon\.[a-z]{2,3}\/albums\/([A-Z0-9]+)"

        downloaded_files = []
        resolved_title = None  # Track title for queue display

        try:
            song_info = None
            original_platform = None
            original_id = None

            if re.search(spotify_track_re, url):
                print("Detected Spotify Track.")
                track_id = re.search(spotify_track_re, url).group(1)
                original_platform = "spotify"
                original_id = track_id
                print(f"  Resolving Deezer ID via Songlink...")
                deezer_id = await self._get_deezer_id_from_songlink(track_id, "spotify", "song")
                # Verify album matches; if not, search Deezer directly with correct album
                if deezer_id:
                    spotify_meta = self._get_spotify_track_metadata(track_id)
                    if spotify_meta and spotify_meta.get('album'):
                        deezer_details = await self.deezer_api.get_deezer_track_details(deezer_id)
                        if deezer_details:
                            deezer_album = (deezer_details.get("album") or "").lower().strip()
                            source_album = spotify_meta["album"].lower().strip()
                            if deezer_album != source_album and source_album not in deezer_album and deezer_album not in source_album:
                                print(f"  Album mismatch: Deezer has '{deezer_details.get('album')}', Spotify has '{spotify_meta['album']}'")
                                print(f"  Searching Deezer for correct album version...")
                                better_link = await self.deezer_api.get_deezer_track_link(
                                    spotify_meta['artist'], spotify_meta['title'], album=spotify_meta['album'])
                                if better_link:
                                    match = re.search(r'deezer\.com\/track\/(\d+)', better_link)
                                    if match:
                                        deezer_id = match.group(1)
                                        print(f"  Found correct version: Deezer ID {deezer_id}")
                if deezer_id:
                    print(f"  Deezer ID: {deezer_id}")
                    song_info = {'deezer_id': deezer_id, 'type': 'track'}
                else:
                    print(f"  Could not find Deezer ID for Spotify track {track_id}", file=sys.stderr)
                    return []
            elif re.search(spotify_album_re, url):
                print("Detected Spotify Album.")
                album_id = re.search(spotify_album_re, url).group(1)
                original_platform = "spotify"
                original_id = album_id
                print(f"  Resolving Deezer ID via Songlink...")
                deezer_id = await self._get_deezer_id_from_songlink(album_id, "spotify", "album")
                if deezer_id:
                    print(f"  Deezer ID: {deezer_id}")
                    song_info = {'deezer_id': deezer_id, 'type': 'album'}
                else:
                    print(f"  Could not find Deezer ID for Spotify album {album_id}", file=sys.stderr)
                    return []
            elif re.search(spotify_playlist_re, url):
                print("Detected Spotify Playlist.")
                playlist_id = re.search(spotify_playlist_re, url).group(1)
                original_platform = "spotify"
                original_id = playlist_id
                deezer_id = await self._get_deezer_id_from_songlink(playlist_id, "spotify", "playlist")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'playlist'}
                else:
                    print(f"Could not find Deezer ID for Spotify playlist {playlist_id}", file=sys.stderr)
                    return []
            elif re.search(deezer_track_re, url):
                print("Detected Deezer Track.")
                track_id = re.search(deezer_track_re, url).group(1)
                song_info = {'deezer_id': track_id, 'type': 'track'}
            elif re.search(deezer_album_re, url):
                print("Detected Deezer Album.")
                album_id = re.search(deezer_album_re, url).group(1)
                song_info = {'deezer_id': album_id, 'type': 'album'}
            elif re.search(deezer_playlist_re, url):
                print("Detected Deezer Playlist.")
                playlist_id = re.search(deezer_playlist_re, url).group(1)
                song_info = {'deezer_id': playlist_id, 'type': 'playlist'}
            elif re.search(deezer_short_re, url):
                print("Detected Deezer Short Link.")
                short_code = re.search(deezer_short_re, url).group(1)
                # Resolve short links to get the actual Deezer URL
                resolved_url = self._resolve_deezer_short_link(short_code)
                if resolved_url:
                    if '/track/' in resolved_url:
                        match = re.search(r'/track/(\d+)', resolved_url)
                        if match:
                            song_info = {'deezer_id': match.group(1), 'type': 'track'}
                    elif '/album/' in resolved_url:
                        match = re.search(r'/album/(\d+)', resolved_url)
                        if match:
                            song_info = {'deezer_id': match.group(1), 'type': 'album'}
                    elif '/playlist/' in resolved_url:
                        match = re.search(r'/playlist/(\d+)', resolved_url)
                        if match:
                            song_info = {'deezer_id': match.group(1), 'type': 'playlist'}
                    else:
                        print(f"Could not determine type from resolved Deezer URL: {resolved_url}", file=sys.stderr)
                        return []
                else:
                    print(f"Could not resolve Deezer short link: {short_code}", file=sys.stderr)
                    return []
            elif re.search(youtube_playlist_re, url):
                print("Detected YouTube (or YouTube Music) Playlist/Album Link.")
                playlist_id = re.search(youtube_playlist_re, url).group(1)
                original_platform = "youtubeMusic" if "music.youtube.com" in url else "youtube"
                original_id = playlist_id
                
                # Resolve as album first
                print(f"Attempting to resolve {original_platform} playlist ID {playlist_id} as 'album' type.", file=sys.stderr)
                deezer_id = await self._get_deezer_id_from_songlink(playlist_id, original_platform, "album")
                
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'album'}
                    print(f"Successfully resolved {original_platform} playlist ID {playlist_id} as Deezer album ID {deezer_id}.", file=sys.stderr)
                else:
                    print(f"Failed to resolve {original_platform} playlist ID {playlist_id} as album. Attempting as 'playlist' type.", file=sys.stderr)
                    # Fallback w/ playlist
                    deezer_id = await self._get_deezer_id_from_songlink(playlist_id, original_platform, "playlist")
                    if deezer_id:
                        song_info = {'deezer_id': deezer_id, 'type': 'playlist'}
                        print(f"Successfully resolved {original_platform} playlist ID {playlist_id} as Deezer playlist ID {deezer_id}.", file=sys.stderr)
                    else:
                        print(f"Could not find Deezer ID for {original_platform} playlist {playlist_id} via direct Songlink resolution for type 'playlist'.", file=sys.stderr)
                        # Fallback if Songlink failed for album and playlist
                        print(f"Attempting resilient fallback for {original_platform} playlist ID {playlist_id} by getting metadata and then searching Deezer.", file=sys.stderr)
                        media_metadata = self._get_media_metadata_from_songlink(playlist_id, original_platform, "album")
                        
                        if not media_metadata:
                             media_metadata = self._get_media_metadata_from_songlink(playlist_id, original_platform, "playlist")
                        
                        if media_metadata and (media_metadata.get('album') or media_metadata.get('playlist_name')) and media_metadata.get('artist'):
                            artist = media_metadata['artist']
                            album_or_playlist_title = media_metadata.get('album') or media_metadata.get('playlist_name')
                            
                            print(f"Obtained metadata: Artist='{artist}', Title='{album_or_playlist_title}'. Attempting direct Deezer album search.", file=sys.stderr)
                            
                            deezer_album_link = await self.deezer_api.get_deezer_album_link(artist, album_or_playlist_title)
                            if deezer_album_link:
                                match = re.search(r'deezer\.com\/album\/(\d+)', deezer_album_link)
                                if match:
                                    deezer_id = match.group(1)
                                    song_info = {'deezer_id': deezer_id, 'type': 'album'}
                                    print(f"Successfully found Deezer album ID {deezer_id} via direct Deezer search for '{album_or_playlist_title}' by '{artist}'.", file=sys.stderr)
                                else:
                                    print(f"Could not extract Deezer album ID from link: {deezer_album_link}", file=sys.stderr)
                            else:
                                print(f"Direct Deezer album search failed for '{album_or_playlist_title}' by '{artist}'.", file=sys.stderr)

                        if not deezer_id:
                            print(f"Could not find Deezer ID for {original_platform} playlist {playlist_id} after all resilient fallback attempts.", file=sys.stderr)
                            return []
            elif re.search(youtube_re, url):
                print("Detected YouTube (or YouTube Music) Link.")
                video_id = re.search(youtube_re, url).group(1)
                platform = "youtubeMusic" if "music.youtube.com" in url else "youtube"
                original_platform = platform
                original_id = video_id
                # Use Songlink URL-based lookup (most reliable for YouTube)
                deezer_id = await self._get_deezer_id_from_songlink_url(url)
                youtube_meta = None  # Store for queue display
                # Fallback: use video title to search Deezer
                if not deezer_id:
                    try:
                        oembed_resp = requests.get(
                            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
                            timeout=10)
                        if oembed_resp.status_code == 200:
                            oembed_data = oembed_resp.json()
                            yt_title = oembed_data.get("title", "")
                            yt_author = oembed_data.get("author_name", "")
                            if yt_title:
                                # Try "artist - title" split if title contains " - "
                                if " - " in yt_title:
                                    parts = yt_title.split(" - ", 1)
                                    search_artist, search_title = parts[0].strip(), parts[1].strip()
                                else:
                                    search_artist, search_title = yt_author, yt_title
                                # Clean common YouTube suffixes
                                # Remove parenthetical/bracketed tags like (Official Music Video), [Official Audio], etc.
                                search_title = re.sub(r'\s*[\(\[][^)\]]*(?:official|music video|lyric|lyrics|visualizer|audio|video|hd|4k)[^)\]]*[\)\]]', '', search_title, flags=re.IGNORECASE).strip()
                                # Remove ft./feat. clauses
                                search_title = re.sub(r'\s*(ft\.?|feat\.?)\s+.*$', '', search_title, flags=re.IGNORECASE).strip()
                                deezer_link = await self.deezer_api.get_deezer_track_link(search_artist, search_title)
                                if deezer_link:
                                    match = re.search(r'deezer\.com\/track\/(\d+)', deezer_link)
                                    if match:
                                        deezer_id = match.group(1)
                                        youtube_meta = {'artist': search_artist, 'title': search_title}
                    except Exception as e:
                        print(f"  YouTube oEmbed fallback failed: {e}", file=sys.stderr)
                if deezer_id:
                    print(f"  Deezer ID: {deezer_id}")
                    song_info = {'deezer_id': deezer_id, 'type': 'track'}
                    # Update queue with YouTube metadata if we got it from oEmbed
                    if youtube_meta:
                        resolved_title = f"{youtube_meta['artist']} - {youtube_meta['title']}"
                        update_status_file(download_id, "in_progress",
                                           f"Downloading: {resolved_title}",
                                           title=resolved_title)
                else:
                    print(f"Could not find Deezer ID for YouTube video {video_id}", file=sys.stderr)
                    return []
            elif re.search(apple_track_re, url):
                print("Detected Apple Music Track.")
                track_id = re.search(apple_track_re, url).group(1)
                original_platform = "appleMusic"
                original_id = track_id
                deezer_id = await self._get_deezer_id_from_songlink(track_id, "appleMusic", "song")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'track'}
                else:
                    print(f"Could not find Deezer ID for Apple Music track {track_id}", file=sys.stderr)
                    return []
            elif re.search(apple_album_re, url):
                print("Detected Apple Music Album.")
                album_id = re.search(apple_album_re, url).group(1)
                original_platform = "appleMusic"
                original_id = album_id
                deezer_id = await self._get_deezer_id_from_songlink(album_id, "appleMusic", "album")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'album'}
                else:
                    print(f"Could not find Deezer ID for Apple Music album {album_id}", file=sys.stderr)
                    return []
            elif re.search(tidal_track_re, url):
                print("Detected Tidal Track.")
                track_id = re.search(tidal_track_re, url).group(1)
                original_platform = "tidal"
                original_id = track_id
                deezer_id = await self._get_deezer_id_from_songlink(track_id, "tidal", "song")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'track'}
                else:
                    print(f"Could not find Deezer ID for Tidal track {track_id}", file=sys.stderr)
                    return []
            elif re.search(tidal_album_re, url):
                print("Detected Tidal Album.")
                album_id = re.search(tidal_album_re, url).group(1)
                original_platform = "tidal"
                original_id = album_id
                deezer_id = await self._get_deezer_id_from_songlink(album_id, "tidal", "album")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'album'}
                else:
                    print(f"Could not find Deezer ID for Tidal album {album_id}", file=sys.stderr)
                    return []
            elif re.search(amazon_track_re, url):
                print("Detected Amazon Music Track.")
                track_id = re.search(amazon_track_re, url).group(1)
                original_platform = "amazonMusic"
                original_id = track_id
                deezer_id = await self._get_deezer_id_from_songlink(track_id, "amazonMusic", "song")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'track'}
                else:
                    print(f"Could not find Deezer ID for Amazon Music track {track_id}", file=sys.stderr)
                    return []
            elif re.search(amazon_album_re, url):
                print("Detected Amazon Music Album.")
                album_id = re.search(amazon_album_re, url).group(1)
                original_platform = "amazonMusic"
                original_id = album_id
                deezer_id = await self._get_deezer_id_from_songlink(album_id, "amazonMusic", "album")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'album'}
                else:
                    print(f"Could not find Deezer ID for Amazon Music album {album_id}", file=sys.stderr)
                    return []
            else:
                print(f"❌ Unsupported or invalid music URL: {url}", file=sys.stderr)
                return []

            if not song_info:
                print(f"Could not get song info for {url}", file=sys.stderr)
                return []

            # --- Duplicate detection ---
            salt, token = self.navidrome_api._get_navidrome_auth_params()
            media_type = song_info['type']

            if media_type == "track":
                print(f"  Fetching Deezer track details for duplicate check...")
                deezer_details = await self.deezer_api.get_deezer_track_details(song_info['deezer_id'])
                if deezer_details:
                    search_artist = deezer_details.get("album_artist") or (deezer_details.get("artists", [None])[0])
                    search_title = deezer_details.get("title")
                    search_album = deezer_details.get("album")
                    print(f"  Deezer metadata: {search_artist} - {search_title} [{search_album}]")
                    # Update queue with resolved track name
                    resolved_title = f"{search_artist} - {search_title}"
                    update_status_file(download_id, "in_progress", f"Downloading: {resolved_title}",
                                       title=resolved_title)
                    if search_artist and search_title:
                        print(f"  Checking Navidrome for existing match...")
                        existing = self.navidrome_api._search_song_in_navidrome(search_artist, search_title, salt, token, album=search_album)
                        if existing:
                            matched_album = existing.get('album', '?')
                            print(f"  Already in Navidrome: {search_artist} - {search_title} [{matched_album}] (id={existing['id']})")
                            update_status_file(download_id, "completed", f"Already in library: {search_artist} - {search_title}",
                                               title=f"{search_artist} - {search_title}")
                            return []
                        print(f"  Not found in library, proceeding with download.")

            elif media_type == "album":
                # Get album tracklist from Deezer and check which tracks already exist
                album_resp = await self.deezer_api._make_request_with_retries(f"https://api.deezer.com/album/{song_info['deezer_id']}/tracks")
                if album_resp and album_resp.status_code == 200:
                    album_tracks = album_resp.json().get("data", [])
                    if album_tracks:
                        all_exist = True
                        for t in album_tracks:
                            t_artist = t.get("artist", {}).get("name", "")
                            t_title = t.get("title", "")
                            if t_artist and t_title:
                                if not self.navidrome_api._search_song_in_navidrome(t_artist, t_title, salt, token):
                                    all_exist = False
                                    break
                            else:
                                all_exist = False
                                break
                        if all_exist:
                            print(f"  All {len(album_tracks)} album tracks already in Navidrome, skipping download.")
                            update_status_file(download_id, "completed", f"Album already in library ({len(album_tracks)} tracks).")
                            return []

            # Download using streamrip for all Deezer IDs
            print(f"  Logging into Deezer...")
            await self.deezer_client.login()

            media_type = song_info['type']
            print(f"  Preparing streamrip {media_type} download (Deezer ID: {song_info['deezer_id']})...")
            if media_type == "track":
                pending = PendingSingle(id=song_info['deezer_id'], client=self.deezer_client, config=self.streamrip_config, db=self.rip_db)
            elif media_type == "album":
                pending = PendingAlbum(id=song_info['deezer_id'], client=self.deezer_client, config=self.streamrip_config, db=self.rip_db)
            elif media_type == "playlist":
                pending = PendingPlaylist(id=song_info['deezer_id'], client=self.deezer_client, config=self.streamrip_config, db=self.rip_db)

            media = None
            try:
                print(f"  Resolving media...")
                media = await pending.resolve()
            except Exception as e:
                print(f"  Streamrip resolution failed for Deezer ID {song_info['deezer_id']}: {e}", file=sys.stderr)
                media = None

            if media:
                if hasattr(media, 'meta'):
                    meta = media.meta
                    print(f"  Downloading: {getattr(meta, 'artist', '?')} - {getattr(meta, 'title', '?')} [{getattr(meta, 'album', '?')}]")
                # Snapshot files before rip so we can find new ones after
                files_before = self._snapshot_audio_files()
                await media.rip()
                print(f"  Streamrip download complete.")
                files_after = self._snapshot_audio_files()
                # Detect files that are new or were modified (overwritten in place)
                new_files = []
                for fp, (mtime, size) in files_after.items():
                    before = files_before.get(fp)
                    if before is None or before != (mtime, size):
                        new_files.append(fp)
                # Also use media.download_path as authoritative if it exists
                if not new_files and hasattr(media, 'download_path') and media.download_path:
                    dp = str(media.download_path)
                    if os.path.exists(dp):
                        new_files = [dp]

                print(f"  Found {len(new_files)} new/modified file(s) after download.")
                if new_files:
                    for f in new_files:
                        print(f"    -> {f}")
                    downloaded_files.extend(new_files)
                else:
                    print(f"  No new files detected via snapshot, trying fallback...")
                    # Fallback: try the old name-based matching
                    if media_type == "track":
                        artist = media.meta.artist if hasattr(media.meta, 'artist') else ''
                        title = media.meta.title if hasattr(media.meta, 'title') else ''
                        downloaded_files.extend(self._find_downloaded_files(artist, title))
                    elif media_type == "album":
                        artist = media.meta.albumartist if hasattr(media.meta, 'albumartist') else ''
                        album_title = media.meta.album if hasattr(media.meta, 'album') else ''
                        downloaded_files.extend(self._find_downloaded_files_for_album(artist, album_title))

                if media_type == "playlist":
                    # If this is a ListenBrainz recommendation, retag the files with the correct comment
                    if lb_recommendation:
                        for file_path in new_files or downloaded_files:
                            if file_path and os.path.exists(file_path):
                                self.tagger.add_comment_to_file(file_path, self.tagger.target_comment)
            else:
                # Streamrip resolution failed — try TrackDownloader (deemix) as fallback
                if media_type == "track":
                    full_song_info = {
                        'deezer_id': song_info['deezer_id'],
                        'artist': '', 'title': '', 'album': '',
                        'release_date': '', 'recording_mbid': '',
                        'source': 'Deezer', 'album_art': ''
                    }

                    # Get metadata from Deezer API
                    deezer_resp = await self.deezer_api._make_request_with_retries(f"{self.deezer_api.track_url_base}{song_info['deezer_id']}")
                    if deezer_resp and deezer_resp.status_code == 200:
                        track_data = deezer_resp.json()
                        full_song_info['artist'] = track_data.get('artist', {}).get('name', '')
                        full_song_info['title'] = track_data.get('title', '')
                        if track_data.get('album'):
                            full_song_info['album'] = track_data['album'].get('title', '')
                            full_song_info['release_date'] = track_data['album'].get('release_date', '')
                            if track_data['album'].get('cover_xl'):
                                full_song_info['album_art'] = track_data['album']['cover_xl']

                    # Fallback to Songlink for metadata if Deezer didn't have it
                    if not full_song_info.get('artist') and original_platform and original_id:
                        songlink_metadata = self._get_media_metadata_from_songlink(original_id, original_platform, "song")
                        if songlink_metadata:
                            full_song_info['artist'] = songlink_metadata.get('artist', '')
                            full_song_info['title'] = songlink_metadata.get('title', '')
                            full_song_info['source'] = original_platform.capitalize()

                    if full_song_info.get('artist') and full_song_info.get('title'):
                        downloaded_track_path = await self.track_downloader.download_track(full_song_info, lb_recommendation=lb_recommendation)
                        if downloaded_track_path:
                            downloaded_files.append(downloaded_track_path)
                            media = True

                elif media_type == "album":
                    album_deezer_id = song_info['deezer_id']
                    album_resp = await self.deezer_api._make_request_with_retries(f"https://api.deezer.com/album/{album_deezer_id}")
                    album_data = album_resp.json() if album_resp and album_resp.status_code == 200 else None

                    if album_data:
                        album_title = album_data.get('title', 'Unknown Album')
                        album_artist = album_data.get('artist', {}).get('name', 'Unknown Artist')
                        album_release_date = album_data.get('release_date', '')
                        album_art = album_data.get('cover_xl', '')

                        tracks_data = await self.deezer_api.get_deezer_album_tracklist_by_search(album_artist, album_title)
                        if tracks_data:
                            for track_item in tracks_data:
                                full_song_info = {
                                    'deezer_id': str(track_item.get('id', '')),
                                    'artist': track_item.get('artist', album_artist),
                                    'title': track_item.get('title', 'Unknown Track'),
                                    'album': album_title,
                                    'release_date': album_release_date,
                                    'recording_mbid': '',
                                    'source': 'Deezer',
                                    'album_art': album_art
                                }
                                downloaded_track_path = await self.track_downloader.download_track(full_song_info, lb_recommendation=lb_recommendation)
                                if downloaded_track_path:
                                    downloaded_files.append(downloaded_track_path)
                            if downloaded_files:
                                media = True

                if not media:
                    print(f"Failed to download Deezer ID {song_info['deezer_id']} after all fallback attempts.", file=sys.stderr)
                    return []

            # After downloading, organize files
            if downloaded_files:
                print("Organizing downloaded files...")
                self.navidrome_api.organize_music_files(self.temp_download_folder, self.music_library_path)
                print(f"Successfully downloaded and organized {len(downloaded_files)} files from {url}")
                if resolved_title:
                    update_status_file(download_id, "completed", f"Downloaded: {resolved_title}", title=resolved_title)
                else:
                    update_status_file(download_id, "completed", f"Downloaded {len(downloaded_files)} files.")
                return downloaded_files
            else:
                print(f"No files were downloaded from {url}", file=sys.stderr)
                update_status_file(download_id, "failed", f"No files downloaded from {url}. The track may not be available on Deezer.")
                return []

        except Exception as e:
            print(f"Unexpected error during download from {url}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return []
        finally:
            try:
                if hasattr(self.deezer_client, 'session') and self.deezer_client.session:
                    await self.deezer_client.session.close()
            except Exception as e:
                print(f"Error closing Deezer client session: {e}", file=sys.stderr)

    def _get_media_metadata_from_songlink(self, item_id, platform, type_param="song"):
        """Use Songlink API to get media metadata (song, album, playlist) from other platform ID."""
        try:
            songlink_url = f"{self.songlink_base_url}/links?platform={platform}&type={type_param}&id={item_id}"
            response = requests.get(songlink_url)
            if response.status_code == 200:
                data = response.json()
                entities = data.get('entitiesByUniqueId', {})
                for entity_key, entity in entities.items():
                    if entity.get('id') == item_id and entity.get('type') == type_param:
                        metadata = {
                            'artist': entity.get('artistName', '') if type_param == 'song' else '',
                            'title': entity.get('title', '') if type_param == 'song' else '',
                            'album': entity.get('title', '') if type_param == 'album' else '',
                            'playlist_name': entity.get('title', '') if type_param == 'playlist' else '',
                            'source': platform,
                            'thumbnailUrl': entity.get('thumbnailUrl', '')
                        }
                        return metadata
                print(f"No {type_param} entity found for {platform} ID {item_id}", file=sys.stderr)
            else:
                print(f"Songlink API request failed with status {response.status_code}: {response.text}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Error calling Songlink API: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

    def _get_spotify_track_metadata(self, track_id):
        """Fetch artist, title, and album name from Spotify for a track ID."""
        try:
            from downloaders.playlist_downloader import _get_spotify_client_token
            token = _get_spotify_client_token()
            if not token:
                return None
            resp = requests.get(
                f"https://api.spotify.com/v1/tracks/{track_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                artist = ", ".join(a["name"] for a in data.get("artists", []))
                title = data.get("name", "")
                album = data.get("album", {}).get("name", "")
                return {"artist": artist, "title": title, "album": album}
        except Exception as e:
            print(f"  Could not fetch Spotify metadata: {e}", file=sys.stderr)
        return None

    async def _get_deezer_id_from_songlink_url(self, source_url):
        """Use Songlink API with a full URL to get Deezer ID. More reliable than platform+id for YouTube."""
        try:
            api_url = f"{self.songlink_base_url}/links?url={requests.utils.quote(source_url, safe='')}"
            response = requests.get(api_url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                deezer_info = data.get('linksByPlatform', {}).get('deezer')
                if deezer_info and deezer_info.get('url'):
                    deezer_url = deezer_info['url']
                    match = re.search(r'deezer\.com\/(?:track|album|playlist)\/(\d+)', deezer_url)
                    if match:
                        return match.group(1)
        except Exception as e:
            print(f"  Songlink URL lookup failed: {e}", file=sys.stderr)
        return None

    async def _get_deezer_id_from_songlink(self, item_id, platform, type_param="song"):
        """Use Songlink API to get Deezer ID from other platform ID, with improved fallback."""
        try:
            url = f"{self.songlink_base_url}/links?platform={platform}&type={type_param}&id={item_id}"
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()

                deezer_info = data.get('linksByPlatform', {}).get('deezer')
                if deezer_info and deezer_info.get('url'):
                    deezer_url = deezer_info['url']
                    match = re.search(r'deezer\.com\/(?:track|album|playlist)\/(\d+)', deezer_url)
                    if match:
                        return match.group(1)
                    print(f"Could not extract ID from Deezer URL: {deezer_url}", file=sys.stderr)

                # If no Deezer link found, attempt direct Deezer API search via metadata
                if type_param in ("album", "song"):
                    media_metadata = self._get_media_metadata_from_songlink(item_id, platform, type_param)
                    if media_metadata:
                        if type_param == "album":
                            artist = media_metadata.get('artist', '')
                            album_title = media_metadata.get('album', '')
                            if artist and album_title:
                                deezer_album_link = await self.deezer_api.get_deezer_album_link(artist, album_title)
                                if deezer_album_link:
                                    match = re.search(r'deezer\.com\/album\/(\d+)', deezer_album_link)
                                    if match:
                                        return match.group(1)
                        elif type_param == "song":
                            artist = media_metadata.get('artist', '')
                            title = media_metadata.get('title', '')
                            if artist and title:
                                deezer_track_link = await self.deezer_api.get_deezer_track_link(artist, title)
                                if deezer_track_link:
                                    match = re.search(r'deezer\.com\/track\/(\d+)', deezer_track_link)
                                    if match:
                                        return match.group(1)

                print(f"No Deezer ID found for {platform} {type_param} {item_id}", file=sys.stderr)
            else:
                print(f"Songlink API failed ({response.status_code}) for {platform} {type_param} {item_id}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Error resolving Deezer ID via Songlink for {platform} {type_param} {item_id}: {e}", file=sys.stderr)
            return None
    def _snapshot_audio_files(self):
        """Return a dict of audio file path -> (mtime, size) in the temp download folder."""
        audio_extensions = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")
        files = {}
        for root, _, filenames in os.walk(self.temp_download_folder):
            for f in filenames:
                if f.endswith(audio_extensions):
                    fp = os.path.join(root, f)
                    try:
                        st = os.stat(fp)
                        files[fp] = (st.st_mtime, st.st_size)
                    except OSError:
                        pass
        return files

    def _find_downloaded_files(self, artist, title):
        """Helper to find a single downloaded file based on artist and title."""
        sanitized_artist = sanitize_filename(artist).lower()
        sanitized_title = sanitize_filename(title).lower()
        output_dir = self.temp_download_folder

        for root, _, files in os.walk(output_dir):
            for filename in files:
                if sanitized_artist in sanitize_filename(filename).lower() and \
                   sanitized_title in sanitize_filename(filename).lower() and \
                   filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                    return [os.path.join(root, filename)]
        return []

    def _find_downloaded_files_for_album(self, artist, album_title):
        """Helper to find all downloaded files for an album based on artist and album title."""
        sanitized_artist = sanitize_filename(artist).lower()
        sanitized_album = sanitize_filename(album_title).lower()
        output_dir = self.temp_download_folder
        found_files = []
        for root, _, files in os.walk(output_dir):
            for filename in files:
                if sanitized_artist in sanitize_filename(filename).lower() and \
                   sanitized_album in sanitize_filename(root).lower() and \
                   filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                    found_files.append(os.path.join(root, filename))
        return found_files

    def _find_downloaded_files_for_playlist(self, playlist_name):
        """Helper to find all downloaded files for a playlist based on playlist name."""
        sanitized_playlist = sanitize_filename(playlist_name).lower()
        output_dir = self.temp_download_folder
        found_files = []
        for root, _, files in os.walk(output_dir):
            for filename in files:
                if sanitized_playlist in sanitize_filename(root).lower() and \
                   filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                    found_files.append(os.path.join(root, filename))
        return found_files

    def _resolve_deezer_short_link(self, short_code):
        """Resolve a Deezer short link to the actual Deezer URL."""
        try:
            short_url = f"https://link.deezer.com/s/{short_code}"
            response = requests.get(short_url, allow_redirects=True)
            if response.status_code == 200:
                final_url = response.url
                print(f"Resolved short link {short_code} to: {final_url}")
                return final_url
            else:
                print(f"Failed to resolve short link {short_code}: HTTP {response.status_code}", file=sys.stderr)
                return None
        except Exception as e:
            print(f"Error resolving Deezer short link {short_code}: {e}", file=sys.stderr)
            return None

import os
import asyncio
import re
import sys
import requests
import json # For pretty printing JSON in debug
from streamrip.client import DeezerClient
from streamrip.media import PendingSingle, PendingAlbum, PendingPlaylist
from streamrip.config import Config
from streamrip.db import Database, Downloads, Failed
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
        self.rip_db = Database(downloads=Downloads("/app/temp_downloads/downloads.db"), failed=Failed("/app/temp_downloads/failed_downloads.db"))
        self.songlink_base_url = "https://api.song.link/v1-alpha.1"

    async def download_from_url(self, url: str, lb_recommendation: bool = False, download_id: Optional[str] = None):
        print(f"Attempting to download from URL: {url}")

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

        try:
            song_info = None
            original_platform = None
            original_id = None

            if re.search(spotify_track_re, url):
                print("Detected Spotify Track.")
                track_id = re.search(spotify_track_re, url).group(1)
                original_platform = "spotify"
                original_id = track_id
                deezer_id = await self._get_deezer_id_from_songlink(track_id, "spotify", "song")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'track'}
                else:
                    print(f"Could not find Deezer ID for Spotify track {track_id}", file=sys.stderr)
                    return []
            elif re.search(spotify_album_re, url):
                print("Detected Spotify Album.")
                album_id = re.search(spotify_album_re, url).group(1)
                original_platform = "spotify"
                original_id = album_id
                deezer_id = await self._get_deezer_id_from_songlink(album_id, "spotify", "album")
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'album'}
                else:
                    print(f"Could not find Deezer ID for Spotify album {album_id}", file=sys.stderr)
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
                deezer_id = await self._get_deezer_id_from_songlink(video_id, platform)
                if deezer_id:
                    song_info = {'deezer_id': deezer_id, 'type': 'track'}
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

            # Download using streamrip for all Deezer IDs
            print(f"DEBUG: Attempting Deezer client login...", file=sys.stderr)
            await self.deezer_client.login()
            print(f"DEBUG: Deezer client login complete.", file=sys.stderr)
            
            media_type = song_info['type']
            if media_type == "track":
                print(f"DEBUG: Creating PendingSingle for Deezer ID: {song_info['deezer_id']}", file=sys.stderr)
                pending = PendingSingle(id=song_info['deezer_id'], client=self.deezer_client, config=self.streamrip_config, db=self.rip_db)
            elif media_type == "album":
                print(f"DEBUG: Creating PendingAlbum for Deezer ID: {song_info['deezer_id']}", file=sys.stderr)
                pending = PendingAlbum(id=song_info['deezer_id'], client=self.deezer_client, config=self.streamrip_config, db=self.rip_db)
            elif media_type == "playlist":
                print(f"DEBUG: Creating PendingPlaylist for Deezer ID: {song_info['deezer_id']}", file=sys.stderr)
                pending = PendingPlaylist(id=song_info['deezer_id'], client=self.deezer_client, config=self.streamrip_config, db=self.rip_db)

            print(f"DEBUG: Attempting to resolve media for Deezer ID: {song_info['deezer_id']} of type: {media_type}", file=sys.stderr)
            media = None
            try:
                media = await pending.resolve()
                print(f"DEBUG: Result of pending.resolve() for Deezer ID {song_info['deezer_id']}: {media}", file=sys.stderr)
            except Exception as e:
                print(f"DEBUG: Streamrip Pending media resolution failed for Deezer ID {song_info['deezer_id']} with error: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
                media = None

            if media:
                print(f"DEBUG: Media resolved successfully for Deezer ID: {song_info['deezer_id']}", file=sys.stderr)
                await media.rip()
                if media_type == "track":
                    artist = media.meta.artist if hasattr(media.meta, 'artist') else ''
                    title = media.meta.title if hasattr(media.meta, 'title') else ''
                    downloaded_files.extend(self._find_downloaded_files(artist, title))
                elif media_type == "album":
                    artist = media.meta.albumartist if hasattr(media.meta, 'albumartist') else ''
                    album_title = media.meta.album if hasattr(media.meta, 'album') else ''
                    downloaded_files.extend(self._find_downloaded_files_for_album(artist, album_title))
                elif media_type == "playlist":
                    playlist_name = getattr(media, 'name', '')
                    downloaded_files.extend(self._find_downloaded_files_for_playlist(playlist_name))
            else:
                # If streamrip's resolution failed for a track, use the TrackDownloader fallback.
                if media_type == "track":
                    print(f"DEBUG: Streamrip PendingSingle resolution failed for Deezer ID {song_info['deezer_id']}. Attempting fallback via TrackDownloader (deemix)...", file=sys.stderr)
                    # Prepare song_info for TrackDownloader.
                    full_song_info = {
                        'deezer_id': song_info['deezer_id'],
                        'artist': '',
                        'title': '',
                        'album': '',
                        'release_date': '',
                        'recording_mbid': '', # Not typically available from Deezer API directly for track ID
                        'source': 'Deezer',
                        'album_art': ''
                    }

                    # Attempt to get artist/title/album details from Deezer API using the Deezer ID
                    deezer_track_full_details_response = await self.deezer_api._make_request_with_retries(f"{self.deezer_api.track_url_base}{song_info['deezer_id']}")
                    if deezer_track_full_details_response and deezer_track_full_details_response.status_code == 200:
                        track_data = deezer_track_full_details_response.json()
                        full_song_info['artist'] = track_data.get('artist', {}).get('name', '')
                        full_song_info['title'] = track_data.get('title', '')
                        if track_data.get('album'):
                            full_song_info['album'] = track_data['album'].get('title', '')
                            full_song_info['release_date'] = track_data['album'].get('release_date', '')
                            if track_data['album'].get('cover_xl'):
                                full_song_info['album_art'] = track_data['album']['cover_xl']
                        print(f"DEBUG: Enriched song info from Deezer API for TrackDownloader fallback: {full_song_info}", file=sys.stderr)
                    else:
                        print(f"DEBUG: Could not get full song details from Deezer API for ID {song_info['deezer_id']} for TrackDownloader fallback.", file=sys.stderr)
                    
                    # If still w/o artist or title, try Songlink
                    if not full_song_info.get('artist') and original_platform and original_id:
                        songlink_metadata = self._get_media_metadata_from_songlink(original_id, original_platform, "song")
                        if songlink_metadata:
                            full_song_info['artist'] = songlink_metadata.get('artist', '')
                            full_song_info['title'] = songlink_metadata.get('title', '')
                            full_song_info['source'] = original_platform.capitalize()
                            print(f"DEBUG: Enriched song info from Songlink for TrackDownloader fallback: {full_song_info}", file=sys.stderr)
                        else:
                            print(f"DEBUG: Could not get artist/title from Songlink for {original_platform} ID {original_id} for TrackDownloader fallback.", file=sys.stderr)

                    if full_song_info.get('artist') and full_song_info.get('title'):
                        downloaded_track_path = await self.track_downloader.download_track(full_song_info)
                        if downloaded_track_path:
                            print(f"DEBUG: TrackDownloader fallback successful. Downloaded: {downloaded_track_path}", file=sys.stderr)
                            downloaded_files.append(downloaded_track_path)
                            # Avoiding "Failed to resolve media" message
                            media = True
                        else:
                            print(f"DEBUG: TrackDownloader fallback failed for Deezer ID {song_info['deezer_id']}.", file=sys.stderr)
                    else:
                        print(f"DEBUG: Not enough information (artist/title) to perform TrackDownloader fallback for Deezer ID {song_info['deezer_id']}.", file=sys.stderr)
                
                elif media_type == "album":
                    print(f"DEBUG: Streamrip PendingAlbum resolution failed for Deezer ID {song_info['deezer_id']}. Attempting track-by-track fallback...", file=sys.stderr)
                    album_deezer_id = song_info['deezer_id']

                    # Getting album metadata from Deezer API directly
                    album_metadata_response = await self.deezer_api._make_request_with_retries(f"https://api.deezer.com/album/{album_deezer_id}")
                    album_data = None
                    if album_metadata_response and album_metadata_response.status_code == 200:
                        album_data = album_metadata_response.json()
                        print(f"DEBUG: Fetched album metadata for ID {album_deezer_id}: {album_data.get('title')}", file=sys.stderr)
                    else:
                        print(f"DEBUG: Failed to fetch album metadata for ID {album_deezer_id} from Deezer API.", file=sys.stderr)

                    if album_data:
                        album_title = album_data.get('title', 'Unknown Album')
                        album_artist = album_data.get('artist', {}).get('name', 'Unknown Artist')
                        album_release_date = album_data.get('release_date', '')
                        album_art = album_data.get('cover_xl', '')

                        # Attempt to get tracklist via search if direct album tracks endpoint failed
                        tracks_data = await self.deezer_api.get_deezer_album_tracklist_by_search(album_artist, album_title)
                        if tracks_data:
                            print(f"DEBUG: Found {len(tracks_data)} tracks for album '{album_title}' by '{album_artist}' via search. Downloading track by track...", file=sys.stderr)
                            for track_item in tracks_data:
                                track_artist = track_item.get('artist', album_artist)
                                track_title = track_item.get('title', 'Unknown Track')
                                track_deezer_id = str(track_item.get('id', ''))

                                full_song_info = {
                                    'deezer_id': track_deezer_id,
                                    'artist': track_artist,
                                    'title': track_title,
                                    'album': album_title,
                                    'release_date': album_release_date,
                                    'recording_mbid': '',
                                    'source': 'Deezer',
                                    'album_art': album_art
                                }
                                downloaded_track_path = await self.track_downloader.download_track(full_song_info)
                                if downloaded_track_path:
                                    print(f"DEBUG: TrackDownloader fallback successful for track '{track_title}'. Downloaded: {downloaded_track_path}", file=sys.stderr)
                                    downloaded_files.append(downloaded_track_path)
                                else:
                                    print(f"DEBUG: TrackDownloader fallback failed for track '{track_title}'.", file=sys.stderr)
                            
                            if downloaded_files:
                                media = True
                            else:
                                print(f"DEBUG: No files downloaded for album {album_deezer_id} during track-by-track fallback.", file=sys.stderr)
                        else:
                            print(f"DEBUG: Failed to get tracks data for album ID {album_deezer_id} for track-by-track fallback.", file=sys.stderr)
                    else:
                        print(f"DEBUG: Failed to retrieve album data for ID {album_deezer_id}.", file=sys.stderr)
                
                if not media:
                    print(f"DEBUG: Failed to resolve media for Deezer ID {song_info['deezer_id']} after all attempts (including all fallbacks).", file=sys.stderr)
                    return []

            # After downloading, organize files
            if downloaded_files:
                print("Organizing downloaded files...")
                self.navidrome_api.organize_music_files(self.temp_download_folder, self.music_library_path)
                print(f"Successfully downloaded and organized {len(downloaded_files)} files from {url}")
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
                print(f"Songlink API response data keys: {list(data.keys())}")
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
                        print(f"Found {type_param} info: {metadata}")
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

    async def _get_deezer_id_from_songlink(self, item_id, platform, type_param="song"):
        """Use Songlink API to get Deezer ID from other platform ID, with improved fallback."""        
        try:
            url = f"{self.songlink_base_url}/links?platform={platform}&type={type_param}&id={item_id}"
            print(f"Calling Songlink API: {url}")
            response = requests.get(url)
            print(f"Songlink API response status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                # Full stderr response
                print(f"===== SONGLINK API RESPONSE DEBUG =====", file=sys.stderr)
                print(f"URL: {url}", file=sys.stderr)
                print(f"Platform: {platform}, ID: {item_id}, Type: {type_param}", file=sys.stderr)
                print(f"Status: {response.status_code}", file=sys.stderr)
                print(f"Response data keys: {list(data.keys())}", file=sys.stderr)
                print(f"Full response JSON:", file=sys.stderr)
                import json
                print(json.dumps(data, indent=2), file=sys.stderr)
                print(f"Available platforms: {list(data.get('linksByPlatform', {}).keys()) if 'linksByPlatform' in data else 'None'}", file=sys.stderr)
                print(f"========================================", file=sys.stderr)
                
                deezer_info = data.get('linksByPlatform', {}).get('deezer')
                if deezer_info and deezer_info.get('url'):
                    deezer_url = deezer_info['url']
                    print(f"Found Deezer URL: {deezer_url}")
                    match = re.search(r'deezer\.com\/(?:track|album|playlist)\/(\d+)', deezer_url)
                    if match:
                        deezer_id = match.group(1)
                        print(f"Extracted Deezer ID: {deezer_id}")
                        return deezer_id
                    else:
                        print(f"Could not extract ID from Deezer URL: {deezer_url}", file=sys.stderr)
                
                # If no Deezer link found on Songlink, attempt direct Deezer API search
                if type_param == "album" or type_param == "song":
                    media_metadata = self._get_media_metadata_from_songlink(item_id, platform, type_param)
                    if media_metadata:
                        if type_param == "album":
                            artist = media_metadata.get('artist', '')
                            album_title = media_metadata.get('album', '')
                            if artist and album_title:
                                print(f"Attempting direct Deezer album search for artist: '{artist}', album: '{album_title}'", file=sys.stderr)
                                deezer_album_link = await self.deezer_api.get_deezer_album_link(artist, album_title)
                                if deezer_album_link:
                                    match = re.search(r'deezer\.com\/album\/(\d+)', deezer_album_link)
                                    if match:
                                        deezer_id = match.group(1)
                                        print(f"Found Deezer ID via direct album search: {deezer_id}", file=sys.stderr)
                                        return deezer_id
                        elif type_param == "song":
                            artist = media_metadata.get('artist', '')
                            title = media_metadata.get('title', '')
                            if artist and title:
                                print(f"Attempting direct Deezer track search for artist: '{artist}', title: '{title}'", file=sys.stderr)
                                deezer_track_link = await self.deezer_api.get_deezer_track_link(artist, title)
                                if deezer_track_link:
                                    match = re.search(r'deezer\.com\/track\/(\d+)', deezer_track_link)
                                    if match:
                                        deezer_id = match.group(1)
                                        print(f"Found Deezer ID via direct track search: {deezer_id}", file=sys.stderr)
                                        return deezer_id

                print(f"No Deezer link found in response for {platform} ID {item_id}, and direct Deezer search also failed for type {type_param}", file=sys.stderr)
            else:
                print(f"Songlink API request failed with status {response.status_code}: {response.text}", file=sys.stderr)
            print(f"Failed to get Deezer ID from Songlink API for {platform} ID {item_id}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Error calling Songlink API or direct Deezer search: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None
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

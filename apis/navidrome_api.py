import requests
import hashlib
import os
import sys
import shutil
import asyncio
import json
import time
from tqdm import tqdm
from config import TEMP_DOWNLOAD_FOLDER
from mutagen import File, MutagenError
from mutagen.id3 import ID3, COMM, ID3NoHeaderError, error as ID3Error
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.m4a import M4A
from utils import sanitize_filename

class NavidromeAPI:
    def __init__(self, root_nd, user_nd, password_nd, music_library_path, target_comment, lastfm_target_comment, album_recommendation_comment=None, llm_target_comment=None, listenbrainz_enabled=False, lastfm_enabled=False, llm_enabled=False):
        self.root_nd = root_nd
        self.user_nd = user_nd
        self.password_nd = password_nd
        self.music_library_path = music_library_path
        self.target_comment = target_comment
        self.lastfm_target_comment = lastfm_target_comment
        self.album_recommendation_comment = album_recommendation_comment
        self.llm_target_comment = llm_target_comment
        self.listenbrainz_enabled = listenbrainz_enabled
        self.lastfm_enabled = lastfm_enabled
        self.llm_enabled = llm_enabled

    def _get_navidrome_auth_params(self):
        """Generates authentication parameters for Navidrome."""
        salt = os.urandom(6).hex()
        token = hashlib.md5((self.password_nd + salt).encode('utf-8')).hexdigest()
        return salt, token

    def _get_all_songs(self, salt, token):
        """Fetches all songs from Navidrome."""
        url = f"{self.root_nd}/rest/search3.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'query': '',
            'songCount': 10000
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data['subsonic-response']['status'] == 'ok' and 'searchResult3' in data['subsonic-response']:
            return data['subsonic-response']['searchResult3']['song']
        else:
            print(f"Error fetching songs from Navidrome: {data['subsonic-response']['status']}")
            return []

    def _get_song_details(self, song_id, salt, token):
        """Fetches details of a specific song from Navidrome."""
        url = f"{self.root_nd}/rest/getSong.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'id': song_id
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data['subsonic-response']['status'] == 'ok' and 'song' in data['subsonic-response']:
            return data['subsonic-response']['song']
        else:
            print(f"Error fetching song details from Navidrome: {data.get('subsonic-response', {}).get('status', 'Unknown')}")
            return None

    def _update_song_comment(self, file_path, new_comment):
        """Updates the comment of a song using Mutagen."""
        try:
            audio = File(file_path)
            if audio is None:
                print(f"Could not open audio file with Mutagen: {file_path}")
                return

            if file_path.lower().endswith('.mp3'):
                if audio.tags is None:
                    audio.tags = ID3()
                audio.tags.delall('COMM') # Remove existing comments
                if new_comment:
                    audio.tags.add(COMM(encoding=3, lang='eng', desc='', text=[new_comment]))
            elif file_path.lower().endswith('.flac'):
                audio['comment'] = [new_comment] if new_comment else []
            elif file_path.lower().endswith(('.ogg', '.oga')):
                audio['comment'] = [new_comment] if new_comment else []
            elif file_path.lower().endswith('.m4a'):
                audio['\xa9cmt'] = [new_comment] if new_comment else []
            else:
                print(f"Unsupported file type for comment update: {file_path}")
                return
            
            audio.save()
            print(f"Successfully updated comment for {file_path} with Mutagen.")

        except MutagenError as e:
            print(f"Error updating comment for {file_path} with Mutagen: {e}")
        except Exception as e:
            print(f"An unexpected error occurred while updating comment for {file_path}: {e}")

    def _delete_song(self, song_path):
        """Deletes a song file and provides verbose output. Returns True if deleted, False otherwise."""
        if os.path.exists(song_path):
            if os.path.isfile(song_path):
                try:
                    os.remove(song_path)
                    print(f"Successfully deleted file: {song_path}")
                    return True
                except OSError as e:
                    print(f"Error deleting file {song_path}: {e}")
                    return False
            elif os.path.isdir(song_path):
                print(f"Skipping deletion of directory: {song_path}. Only files are deleted by this function.")
                return False
            else:
                print(f"Path exists but is neither a file nor a directory: {song_path}")
                return False
        else:
            print(f"Attempted to delete, but path does not exist: {song_path}")
            return False

    def _find_actual_song_path(self, navidrome_relative_path, song_details=None):
        """
        Attempts to find the actual file path on disk given the Navidrome relative path.
        Uses a much simpler and more reliable approach.
        """
        # First strat : path as-is first (works for most cases)
        expected_full_path = os.path.join(self.music_library_path, navidrome_relative_path)
        if os.path.exists(expected_full_path):
            return expected_full_path

        # Second strat : reconstructing song details from metadata
        if song_details:
            artist = sanitize_filename(song_details.get('artist', ''))
            album = sanitize_filename(song_details.get('album', ''))
            title = sanitize_filename(song_details.get('title', ''))

            if artist and album and title:
                reconstructed_path = os.path.join(artist, album, f"{title}.mp3")
                reconstructed_full_path = os.path.join(self.music_library_path, reconstructed_path)
                if os.path.exists(reconstructed_full_path):
                    return reconstructed_full_path

                # Trying with track number
                track = song_details.get('track', '')
                if track:
                    reconstructed_path_with_track = os.path.join(artist, album, f"{track} - {title}.mp3")
                    reconstructed_full_path_with_track = os.path.join(self.music_library_path, reconstructed_path_with_track)
                    if os.path.exists(reconstructed_full_path_with_track):
                        return reconstructed_full_path_with_track

        # Third strat : more complex logic function if needed
        return self._find_actual_song_path_fallback(navidrome_relative_path)

    def _find_actual_song_path_fallback(self, navidrome_relative_path):
        """
        Fallback method using the original complex path resolution logic.
        Only used when the cleaner approach fails.
        """
        # Common variations
        modified_relative_path_1 = navidrome_relative_path.replace(" - ", " ")
        full_path_1 = os.path.join(self.music_library_path, modified_relative_path_1)
        if os.path.exists(full_path_1):
            return full_path_1

        modified_relative_path_2 = navidrome_relative_path.replace(" ", " - ")
        full_path_2 = os.path.join(self.music_library_path, modified_relative_path_2)
        if os.path.exists(full_path_2):
            return full_path_2

        # Removing track number prefix
        import re
        track_number_pattern = r'^\d{1,2}\s*-\s*(.+)$'
        match = re.match(track_number_pattern, os.path.basename(navidrome_relative_path))
        if match:
            filename_without_number = match.group(1)
            path_without_number = os.path.join(os.path.dirname(navidrome_relative_path), filename_without_number)
            full_path_3 = os.path.join(self.music_library_path, path_without_number)
            if os.path.exists(full_path_3):
                return full_path_3

        # Different separators in path
        path_parts = navidrome_relative_path.split('/')
        if len(path_parts) >= 2:
            # Just artist/album/filename (w/o track number)
            filename = os.path.basename(navidrome_relative_path)
            match = re.match(track_number_pattern, filename)
            if match:
                clean_filename = match.group(1)
                clean_path = os.path.join(path_parts[0], path_parts[1], clean_filename)
                full_path_4 = os.path.join(self.music_library_path, clean_path)
                if os.path.exists(full_path_4):
                    return full_path_4

        # Additional case variations
        if len(path_parts) >= 2:
            # Case-insensitive artist name matching
            artist_lower = path_parts[0].lower()
            album_lower = path_parts[1].lower()

            # Directories that match case-insensitively
            try:
                for root_dir in os.listdir(self.music_library_path):
                    if root_dir.lower() == artist_lower:
                        artist_actual = root_dir

                        # Now album directory
                        artist_path = os.path.join(self.music_library_path, artist_actual)
                        if os.path.isdir(artist_path):
                            for album_dir in os.listdir(artist_path):
                                if album_dir.lower() == album_lower:
                                    album_actual = album_dir

                                    album_path = os.path.join(artist_path, album_actual)

                                    # W/ & w/o track number
                                    filename = os.path.basename(navidrome_relative_path)
                                    match = re.match(track_number_pattern, filename)
                                    if match:
                                        clean_filename = match.group(1)
                                        file_without_number = os.path.join(album_path, clean_filename)
                                        if os.path.exists(file_without_number):
                                            return file_without_number

                                    # Original filename
                                    file_with_path = os.path.join(album_path, filename)
                                    if os.path.exists(file_with_path):
                                        return file_with_path
            except OSError:
                pass

        # Handling underscore variations in artist names
        if len(path_parts) >= 2:
            artist_part = path_parts[0]
            
            # Removing everything after underscore
            if '_' in artist_part:
                base_artist = artist_part.split('_')[0]
                modified_path = os.path.join(base_artist, *path_parts[1:])
                full_path = os.path.join(self.music_library_path, modified_path)
                if os.path.exists(full_path):
                    return full_path
                
                # Modified path + track number removal
                filename = os.path.basename(modified_path)
                match = re.match(track_number_pattern, filename)
                if match:
                    clean_filename = match.group(1)
                    path_without_number = os.path.join(os.path.dirname(modified_path), clean_filename)
                    full_path_without_number = os.path.join(self.music_library_path, path_without_number)
                    if os.path.exists(full_path_without_number):
                        return full_path_without_number

        return None

    async def process_navidrome_library(self, listenbrainz_api=None, lastfm_api=None):
        """Processes the Navidrome library with a progress bar."""
        salt, token = self._get_navidrome_auth_params()
        all_songs = self._get_all_songs(salt, token)
        print(f"Parsing {len(all_songs)} songs from Navidrome to cleanup badly rated songs.")
        print(f"Looking for comments: '{self.target_comment}' (ListenBrainz), '{self.lastfm_target_comment}' (Last.fm), '{self.album_recommendation_comment}' (Album Recommendation), and '{self.llm_target_comment}' (LLM)")

        deleted_songs = []
        found_comments = []

        for song in tqdm(all_songs, desc="Processing Navidrome Library", unit="song", file=sys.stdout):
            song_details = self._get_song_details(song['id'], salt, token)
            if song_details is None:
                continue

            navidrome_relative_path = song_details['path']
            song_path = self._find_actual_song_path(navidrome_relative_path, song_details)

            if song_path is None:
                continue

            # Check if song has a recommendation comment - first from Navidrome API
            api_comment = song_details.get('comment', '')

            # Check tags for target comment using mutagen
            actual_comment = ""
            try:
                # Use File() to open various audio formats
                audio = File(song_path)
                if audio is None:
                    raise MutagenError("Could not open audio file.")

                if song_path.lower().endswith('.mp3'):
                    # For MP3s, use ID3 tags
                    if audio.tags is None:
                        raise ID3Error("No ID3 tags found.")
                    
                    comm_frames = audio.tags.getall('COMM')

                    for comm_frame in comm_frames:
                        # Try to get text from the frame
                        if comm_frame.text:
                            # Handle both string and list formats
                            if isinstance(comm_frame.text, list):
                                text_value = comm_frame.text[0] if comm_frame.text else None
                            else:
                                text_value = comm_frame.text

                            # Convert to string and strip whitespace
                            if text_value is not None:
                                text_value_str = str(text_value).strip()
                                if text_value_str:
                                    actual_comment = text_value_str
                                    break

                        # Also try accessing via description if no direct text
                        elif hasattr(comm_frame, 'desc') and comm_frame.desc:
                            desc_value = str(comm_frame.desc).strip()
                            if desc_value:
                                actual_comment = desc_value
                                break

                    # Direct access to specific COMM frames if no comment found
                    if not actual_comment:
                        for key in audio.keys():
                            if key.startswith('COMM'):
                                frame = audio[key]
                                if hasattr(frame, 'text') and frame.text:
                                    if isinstance(frame.text, list):
                                        text_value = frame.text[0] if frame.text else None
                                    else:
                                        text_value = frame.text

                                    if text_value is not None:
                                        text_value_str = str(text_value).strip()
                                        if text_value_str:
                                            actual_comment = text_value_str
                                            break
                                # Check description field for language-specific frames
                                elif hasattr(frame, 'desc') and frame.desc:
                                    desc_value = str(frame.desc).strip()
                                    if desc_value:
                                        actual_comment = desc_value
                                        break

            except (ImportError, ID3NoHeaderError, Exception) as e:
                # If mutagen fails or no ID3 tags, fall back to API comment
                actual_comment = api_comment

            
            # Use the actual file comment if available, otherwise use API comment
            song_comment = actual_comment if actual_comment else api_comment
            has_recommendation_comment = (song_comment == self.target_comment or
                                        song_comment == self.lastfm_target_comment or
                                        song_comment == self.album_recommendation_comment or
                                        song_comment == self.llm_target_comment)
            

            
            if has_recommendation_comment and song_path:
                # First check if the song was starred, if so treat it as 5 star rating
                starred = song_details.get("starred")
                user_rating = 5 if starred else song_details.get('userRating', 0)
                
                # ListenBrainz recommendations
                if song_comment == self.target_comment and self.listenbrainz_enabled:
                    if user_rating == 5:
                        self._update_song_comment(song_path, "")
                        # Submit positive feedback (love) for 5-star tracks
                        if 'musicBrainzId' in song_details and song_details['musicBrainzId'] and listenbrainz_api:
                            await listenbrainz_api.submit_feedback(song_details['musicBrainzId'], 1)
                    elif user_rating == 4:
                        # Keep 4-star tracks but remove comment (no feedback)
                        self._update_song_comment(song_path, "")
                    elif user_rating == 1:
                        if os.path.isdir(song_path):
                            all_files_deleted_in_dir = True
                            for root, _, files in os.walk(song_path):
                                for file in files:
                                    file_to_delete = os.path.join(root, file)
                                    if not self._delete_song(file_to_delete):
                                        all_files_deleted_in_dir = False
                            if all_files_deleted_in_dir:
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")
                        else:
                            if self._delete_song(song_path):
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")
                        # Submit negative feedback (hate) for 1-star tracks
                        if 'musicBrainzId' in song_details and song_details['musicBrainzId'] and listenbrainz_api:
                            await listenbrainz_api.submit_feedback(song_details['musicBrainzId'], -1)
                    elif user_rating <= 3:
                        # Delete tracks rated 2-3 stars but don't submit feedback
                        if os.path.isdir(song_path):
                            all_files_deleted_in_dir = True
                            for root, _, files in os.walk(song_path):
                                for file in files:
                                    file_to_delete = os.path.join(root, file)
                                    if not self._delete_song(file_to_delete):
                                        all_files_deleted_in_dir = False
                            if all_files_deleted_in_dir:
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")
                        else:
                            if self._delete_song(song_path):
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")

                # Last.fm recommendations
                elif song_comment == self.lastfm_target_comment and self.lastfm_enabled:
                    if user_rating == 5:
                        self._update_song_comment(song_path, "")
                        # Submit positive feedback (love) for 5-star tracks
                        if lastfm_api:
                            try:
                                await asyncio.to_thread(lastfm_api.love_track, song_details['title'], song_details['artist'])
                            except Exception as e:
                                print(f"Error submitting Last.fm love feedback for {song_details['artist']} - {song_details['title']}: {e}")
                    elif user_rating == 4:
                        # Keep 4-star tracks but remove comment (no feedback)
                        self._update_song_comment(song_path, "")
                    elif user_rating <= 3:
                        if os.path.isdir(song_path):
                            all_files_deleted_in_dir = True
                            for root, _, files in os.walk(song_path):
                                for file in files:
                                    file_to_delete = os.path.join(root, file)
                                    if not self._delete_song(file_to_delete):
                                        all_files_deleted_in_dir = False
                            if all_files_deleted_in_dir:
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")
                        else:
                            if self._delete_song(song_path):
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")

                # Album recommendations
                elif song_comment == self.album_recommendation_comment:
                    if user_rating == 5 or user_rating == 4:
                        # Keep 4-5 star tracks but remove comment (no feedback for albums)
                        self._update_song_comment(song_path, "")
                    elif user_rating <= 3:
                        if os.path.isdir(song_path):
                            all_files_deleted_in_dir = True
                            for root, _, files in os.walk(song_path):
                                for file in files:
                                    file_to_delete = os.path.join(root, file)
                                    if not self._delete_song(file_to_delete):
                                        all_files_deleted_in_dir = False
                            if all_files_deleted_in_dir:
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")
                        else:
                            if self._delete_song(song_path):
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")

                # LLM recommendations
                elif song_comment == self.llm_target_comment and self.llm_enabled:
                    if user_rating >= 4: # Keep 4-5 star tracks
                        self._update_song_comment(song_path, "")
                    elif user_rating <= 3: # Delete tracks rated 3 stars or below
                        if os.path.isdir(song_path):
                            all_files_deleted_in_dir = True
                            for root, _, files in os.walk(song_path):
                                for file in files:
                                    file_to_delete = os.path.join(root, file)
                                    if not self._delete_song(file_to_delete):
                                        all_files_deleted_in_dir = False
                            if all_files_deleted_in_dir:
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")
                        else:
                            if self._delete_song(song_path):
                                deleted_songs.append(f"{song_details['artist']} - {song_details['title']}")

                # When no specific service is enabled, delete all commented songs
                elif not self.listenbrainz_enabled and not self.lastfm_enabled:
                    if os.path.isdir(song_path):
                        all_files_deleted_in_dir = True
                        for root, _, files in os.walk(song_path):
                            for file in files:
                                file_to_delete = os.path.join(root, file)
                                if not self._delete_song(file_to_delete):
                                    all_files_deleted_in_dir = False
                        if all_files_deleted_in_dir:
                            deleted_songs.append(f"{song_details['artist']} - {song_details['title']} (Commented)")
                    else:
                        if self._delete_song(song_path):
                            deleted_songs.append(f"{song_details['artist']} - {song_details['title']} (Commented)")

        if deleted_songs:
            print("Deleting the following songs from last week recommendation playlist:")
            for song in deleted_songs:
                print(f"- {song}")
        else:
            print("No songs with recommendation comment were found.")

        # Remove empty folders after cleanup
        print("Removing empty folders from music library...")
        from utils import remove_empty_folders
        remove_empty_folders(self.music_library_path)
        print("Empty folder removal completed.")


    # ---- Subsonic API Playlist Methods (for API playlist mode) ----

    def _search_song_in_navidrome(self, artist, title, salt, token):
        """Search for a song in Navidrome by artist and title. Returns song dict or None."""
        url = f"{self.root_nd}/rest/search3.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'query': f"{artist} {title}",
            'songCount': 20,
            'artistCount': 0,
            'albumCount': 0
        }
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            songs = data.get('subsonic-response', {}).get('searchResult3', {}).get('song', [])
            # Try exact match first
            for song in songs:
                if (song.get('artist', '').lower() == artist.lower() and
                        song.get('title', '').lower() == title.lower()):
                    return song
            # Fallback: return first result if any
            return songs[0] if songs else None
        except Exception as e:
            print(f"Error searching Navidrome for '{artist} - {title}': {e}")
            return None

    def _get_playlists(self, salt, token):
        """Get all playlists from Navidrome."""
        url = f"{self.root_nd}/rest/getPlaylists.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json'
        }
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            playlists = data.get('subsonic-response', {}).get('playlists', {}).get('playlist', [])
            return playlists
        except Exception as e:
            print(f"Error fetching playlists from Navidrome: {e}")
            return []

    def _find_playlist_by_name(self, name, salt, token):
        """Find a playlist by name. Returns playlist dict or None."""
        playlists = self._get_playlists(salt, token)
        for pl in playlists:
            if pl.get('name') == name:
                return pl
        return None

    def _create_playlist(self, name, song_ids, salt, token):
        """Create a new playlist with the given song IDs."""
        url = f"{self.root_nd}/rest/createPlaylist.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'name': name
        }
        # Add song IDs as repeated params
        param_list = [(k, v) for k, v in params.items()]
        for sid in song_ids:
            param_list.append(('songId', sid))
        try:
            response = requests.get(url, params=param_list)
            response.raise_for_status()
            data = response.json()
            if data.get('subsonic-response', {}).get('status') == 'ok':
                print(f"Created playlist '{name}' with {len(song_ids)} tracks.")
                return True
            else:
                print(f"Error creating playlist '{name}': {data}")
                return False
        except Exception as e:
            print(f"Error creating playlist '{name}': {e}")
            return False

    def _update_playlist(self, playlist_id, song_ids, salt, token):
        """Replace the contents of an existing playlist with the given song IDs.
        Uses createPlaylist with playlistId to overwrite."""
        url = f"{self.root_nd}/rest/createPlaylist.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'playlistId': playlist_id
        }
        param_list = [(k, v) for k, v in params.items()]
        for sid in song_ids:
            param_list.append(('songId', sid))
        try:
            response = requests.get(url, params=param_list)
            response.raise_for_status()
            data = response.json()
            if data.get('subsonic-response', {}).get('status') == 'ok':
                print(f"Updated playlist (id={playlist_id}) with {len(song_ids)} tracks.")
                return True
            else:
                print(f"Error updating playlist (id={playlist_id}): {data}")
                return False
        except Exception as e:
            print(f"Error updating playlist (id={playlist_id}): {e}")
            return False

    def _start_scan(self, salt, token):
        """Trigger a Navidrome library scan via the Subsonic API."""
        url = f"{self.root_nd}/rest/startScan.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json'
        }
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get('subsonic-response', {}).get('status') == 'ok':
                print("Library scan triggered successfully.")
                return True
            else:
                print(f"Error triggering scan: {data}")
                return False
        except Exception as e:
            print(f"Error triggering library scan: {e}")
            return False

    def _get_scan_status(self, salt, token):
        """Check if a library scan is in progress."""
        url = f"{self.root_nd}/rest/getScanStatus.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json'
        }
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            scan_status = data.get('subsonic-response', {}).get('scanStatus', {})
            return scan_status.get('scanning', False)
        except Exception as e:
            print(f"Error checking scan status: {e}")
            return False

    def _wait_for_scan(self, salt, token, timeout=120):
        """Wait for an ongoing library scan to complete."""
        start = time.time()
        while time.time() - start < timeout:
            if not self._get_scan_status(salt, token):
                print("Library scan completed.")
                return True
            time.sleep(3)
        print(f"Scan did not complete within {timeout}s timeout.")
        return False

    def _remove_from_playlist(self, playlist_id, song_index, salt, token):
        """Remove a song from a playlist by its index."""
        url = f"{self.root_nd}/rest/updatePlaylist.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'playlistId': playlist_id,
            'songIndexToRemove': song_index
        }
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error removing song at index {song_index} from playlist {playlist_id}: {e}")
            return False

    def _get_playlist_songs(self, playlist_id, salt, token):
        """Get all songs in a playlist."""
        url = f"{self.root_nd}/rest/getPlaylist.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'f': 'json',
            'id': playlist_id
        }
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            playlist = data.get('subsonic-response', {}).get('playlist', {})
            return playlist.get('entry', [])
        except Exception as e:
            print(f"Error fetching playlist songs for {playlist_id}: {e}")
            return []

    # ---- Download History JSON Management ----

    @staticmethod
    def _load_download_history(history_path):
        """Load the download history JSON. Returns a dict keyed by source name,
        each value is a list of track entries."""
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error loading download history: {e}")
        return {}

    @staticmethod
    def _save_download_history(history_path, history):
        """Save the download history JSON."""
        try:
            os.makedirs(os.path.dirname(history_path) if os.path.dirname(history_path) else '.', exist_ok=True)
            with open(history_path, 'w') as f:
                json.dump(history, f, indent=2)
        except IOError as e:
            print(f"Error saving download history: {e}")

    def add_to_download_history(self, history_path, source_name, track_entry):
        """Add a track entry to the download history for a given source.
        track_entry should be a dict with at least: artist, title, album, file_path"""
        history = self._load_download_history(history_path)
        if source_name not in history:
            history[source_name] = []
        # Avoid duplicates
        for existing in history[source_name]:
            if (existing.get('artist', '').lower() == track_entry.get('artist', '').lower() and
                    existing.get('title', '').lower() == track_entry.get('title', '').lower()):
                # Update file_path if changed
                existing['file_path'] = track_entry.get('file_path', existing.get('file_path'))
                self._save_download_history(history_path, history)
                return
        history[source_name].append(track_entry)
        self._save_download_history(history_path, history)

    def remove_from_download_history(self, history_path, source_name, artist, title):
        """Remove a track from the download history."""
        history = self._load_download_history(history_path)
        if source_name not in history:
            return
        history[source_name] = [
            t for t in history[source_name]
            if not (t.get('artist', '').lower() == artist.lower() and
                    t.get('title', '').lower() == title.lower())
        ]
        self._save_download_history(history_path, history)

    # ---- API Playlist Mode: Update playlists after download ----

    def update_api_playlists(self, downloaded_songs_info, history_path):
        """After downloading, update Navidrome API playlists for each source.
        - Triggers a scan so new files get IDs
        - Groups tracks by source
        - For each source, finds/creates a playlist and sets its contents
        """
        salt, token = self._get_navidrome_auth_params()

        # Trigger scan and wait
        print("Triggering library scan for newly downloaded files...")
        self._start_scan(salt, token)
        self._wait_for_scan(salt, token)

        # Group songs by source
        source_map = {
            'listenbrainz': 'ListenBrainz Recommendations',
            'last.fm': 'Last.fm Recommendations',
            'llm': 'LLM Recommendations',
        }

        tracks_by_source = {}
        for song in downloaded_songs_info:
            src = song.get('source', 'Unknown').lower()
            playlist_name = source_map.get(src, f"{song.get('source', 'Unknown')} Recommendations")
            if playlist_name not in tracks_by_source:
                tracks_by_source[playlist_name] = []
            tracks_by_source[playlist_name].append(song)

        history = self._load_download_history(history_path)

        for playlist_name, songs in tracks_by_source.items():
            print(f"\n--- Updating API playlist: {playlist_name} ---")
            song_ids = []
            for song in songs:
                nd_song = self._search_song_in_navidrome(song['artist'], song['title'], salt, token)
                if nd_song:
                    song_ids.append(nd_song['id'])
                    # Record in download history
                    source_key = song.get('source', 'Unknown')
                    self.add_to_download_history(history_path, source_key, {
                        'artist': song['artist'],
                        'title': song['title'],
                        'album': song.get('album', ''),
                        'navidrome_id': nd_song['id'],
                        'file_path': nd_song.get('path', ''),
                        'recording_mbid': song.get('recording_mbid', ''),
                        'downloaded_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                    })
                else:
                    print(f"  Could not find '{song['artist']} - {song['title']}' in Navidrome after scan.")

            if not song_ids:
                print(f"  No tracks found in Navidrome for playlist '{playlist_name}'. Skipping.")
                continue

            # Find or create playlist
            existing = self._find_playlist_by_name(playlist_name, salt, token)
            if existing:
                self._update_playlist(existing['id'], song_ids, salt, token)
            else:
                self._create_playlist(playlist_name, song_ids, salt, token)

    # ---- API Playlist Mode: Cleanup ----

    async def process_api_cleanup(self, history_path, listenbrainz_api=None, lastfm_api=None):
        """Cleanup routine for API playlist mode.
        Iterates through the download history JSON, checks ratings, and:
        - High rated (4-5 stars) / starred: remove from history (keep file permanently)
        - Low rated (1-3 stars) / unrated: delete file, remove from playlist, remove from history
        """
        salt, token = self._get_navidrome_auth_params()
        history = self._load_download_history(history_path)

        if not history:
            print("No download history found. Nothing to clean up in API mode.")
            return

        deleted_songs = []
        kept_songs = []

        source_map_reverse = {
            'ListenBrainz': self.target_comment,
            'Last.fm': self.lastfm_target_comment,
            'LLM': self.llm_target_comment,
        }

        playlist_name_map = {
            'ListenBrainz': 'ListenBrainz Recommendations',
            'Last.fm': 'Last.fm Recommendations',
            'LLM': 'LLM Recommendations',
        }

        for source_name in list(history.keys()):
            tracks = history.get(source_name, [])
            if not tracks:
                continue

            print(f"\n--- API Cleanup for source: {source_name} ({len(tracks)} tracked downloads) ---")

            playlist_name = playlist_name_map.get(source_name, f"{source_name} Recommendations")
            existing_playlist = self._find_playlist_by_name(playlist_name, salt, token)
            playlist_songs = []
            if existing_playlist:
                playlist_songs = self._get_playlist_songs(existing_playlist['id'], salt, token)

            tracks_to_remove = []
            tracks_to_keep_ids = []  # IDs to keep in playlist

            for track in tracks:
                artist = track.get('artist', '')
                title = track.get('title', '')
                nd_id = track.get('navidrome_id', '')
                file_rel_path = track.get('file_path', '')

                # Get song details from Navidrome for rating
                song_details = None
                if nd_id:
                    song_details = self._get_song_details(nd_id, salt, token)

                if song_details is None:
                    # Song might have been deleted externally, remove from history
                    print(f"  Song not found in Navidrome: {artist} - {title}. Removing from history.")
                    tracks_to_remove.append(track)
                    continue

                starred = song_details.get("starred")
                user_rating = 5 if starred else song_details.get('userRating', 0)

                if user_rating >= 4:
                    # Keep the file, remove from download history (it's now a permanent library file)
                    print(f"  KEEP (rating={user_rating}): {artist} - {title}")
                    kept_songs.append(f"{artist} - {title} (rating={user_rating})")
                    tracks_to_remove.append(track)

                    # Submit feedback for high-rated tracks
                    if source_name == 'ListenBrainz' and self.listenbrainz_enabled and user_rating == 5:
                        mbid = track.get('recording_mbid', '') or song_details.get('musicBrainzId', '')
                        if mbid and listenbrainz_api:
                            await listenbrainz_api.submit_feedback(mbid, 1)
                    elif source_name == 'Last.fm' and self.lastfm_enabled and user_rating == 5:
                        if lastfm_api:
                            try:
                                await asyncio.to_thread(lastfm_api.love_track, title, artist)
                            except Exception as e:
                                print(f"  Error submitting Last.fm love: {e}")

                    # Keep in playlist
                    if nd_id:
                        tracks_to_keep_ids.append(nd_id)
                else:
                    # Delete the file
                    file_path = self._find_actual_song_path(file_rel_path, song_details)
                    if file_path and os.path.exists(file_path):
                        if self._delete_song(file_path):
                            deleted_songs.append(f"{artist} - {title}")
                    else:
                        print(f"  File not found for deletion: {artist} - {title}")
                        deleted_songs.append(f"{artist} - {title} (file not found)")

                    tracks_to_remove.append(track)

                    # Submit negative feedback for 1-star
                    if user_rating == 1:
                        if source_name == 'ListenBrainz' and self.listenbrainz_enabled:
                            mbid = track.get('recording_mbid', '') or song_details.get('musicBrainzId', '')
                            if mbid and listenbrainz_api:
                                await listenbrainz_api.submit_feedback(mbid, -1)

            # Remove processed tracks from history
            for track in tracks_to_remove:
                self.remove_from_download_history(history_path, source_name, track.get('artist', ''), track.get('title', ''))

            # Update the playlist to remove deleted songs (keep only high-rated ones
            # and any pre-existing library songs that the user manually added)
            # We rebuild with only the kept IDs from our tracked downloads
            # Note: we don't touch songs in the playlist that aren't in our history
            if existing_playlist and playlist_songs:
                # Get the set of tracked IDs we processed
                processed_ids = {t.get('navidrome_id', '') for t in tracks}
                # Keep songs that are either: not tracked by us, or explicitly kept
                new_song_ids = []
                for ps in playlist_songs:
                    ps_id = ps.get('id', '')
                    if ps_id not in processed_ids:
                        # Not tracked by us - keep it (pre-existing library song)
                        new_song_ids.append(ps_id)
                    elif ps_id in tracks_to_keep_ids:
                        # Tracked and high-rated - keep it
                        new_song_ids.append(ps_id)
                    # else: tracked and low-rated - remove from playlist

                self._update_playlist(existing_playlist['id'], new_song_ids, salt, token)

        if deleted_songs:
            print("\nDeleted the following songs during API cleanup:")
            for s in deleted_songs:
                print(f"  - {s}")
        if kept_songs:
            print("\nKept the following songs (removed from tracking, now permanent):")
            for s in kept_songs:
                print(f"  - {s}")

        # Remove empty folders
        print("Removing empty folders from music library...")
        from utils import remove_empty_folders
        remove_empty_folders(self.music_library_path)
        print("Empty folder removal completed.")

    def organize_music_files(self, source_folder, destination_base_folder):
        """
        Organizes music files from a source folder into a destination base folder
        using Artist/Album/filename structure based on metadata.
        """
        from mutagen.id3 import ID3, ID3NoHeaderError
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from mutagen.oggvorbis import OggVorbis
        from mutagen.m4a import M4A
        from utils import sanitize_filename

        print(f"\nOrganizing music files from '{source_folder}' to '{destination_base_folder}'...")

        # Supported audio file extensions
        audio_extensions = ('.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wma')

        for root, dirs, files in os.walk(source_folder):
            for filename in files:
                if filename.lower().endswith(audio_extensions):
                    file_path = os.path.join(root, filename)
                    file_ext = os.path.splitext(filename)[1].lower()

                    try:
                        # Extract metadata based on file type
                        if file_ext == '.mp3':
                            audio = ID3(file_path)
                            artist = str(audio.get('TPE1', ['Unknown Artist'])[0])
                            album = str(audio.get('TALB', ['Unknown Album'])[0])
                            title = str(audio.get('TIT2', [os.path.splitext(filename)[0]])[0])
                        elif file_ext == '.flac':
                            audio = FLAC(file_path)
                            artist_tag = audio.get('artist')
                            if artist_tag:
                                artist = artist_tag[0] if isinstance(artist_tag, list) else str(artist_tag)
                            else:
                                artist = 'Unknown Artist'
                            album_tag = audio.get('album')
                            if album_tag:
                                album = album_tag[0] if isinstance(album_tag, list) else str(album_tag)
                            else:
                                album = 'Unknown Album'
                            title_tag = audio.get('title')
                            if title_tag:
                                title = title_tag[0] if isinstance(title_tag, list) else str(title_tag)
                            else:
                                title = os.path.splitext(filename)[0]
                        elif file_ext in ('.m4a', '.aac'):
                            audio = M4A(file_path)
                            artist_tag = audio.get('\xa9ART')
                            if artist_tag:
                                artist = artist_tag[0] if isinstance(artist_tag, list) else str(artist_tag)
                            else:
                                artist = 'Unknown Artist'
                            album_tag = audio.get('\xa9alb')
                            if album_tag:
                                album = album_tag[0] if isinstance(album_tag, list) else str(album_tag)
                            else:
                                album = 'Unknown Album'
                            title_tag = audio.get('\xa9nam')
                            if title_tag:
                                title = title_tag[0] if isinstance(title_tag, list) else str(title_tag)
                            else:
                                title = os.path.splitext(filename)[0]
                        elif file_ext in ('.ogg', '.wma'):
                            audio = OggVorbis(file_path)
                            artist_tag = audio.get('artist')
                            if artist_tag:
                                artist = artist_tag[0] if isinstance(artist_tag, list) else str(artist_tag)
                            else:
                                artist = 'Unknown Artist'
                            album_tag = audio.get('album')
                            if album_tag:
                                album = album_tag[0] if isinstance(album_tag, list) else str(album_tag)
                            else:
                                album = 'Unknown Album'
                            title_tag = audio.get('title')
                            if title_tag:
                                title = title_tag[0] if isinstance(title_tag, list) else str(title_tag)
                            else:
                                title = os.path.splitext(filename)[0]
                        else:
                            # Fallback for unsupported formats
                            artist = "Unknown Artist"
                            album = "Unknown Album"
                            title = os.path.splitext(filename)[0]

                        artist = sanitize_filename(artist)
                        album = sanitize_filename(album)
                        title = sanitize_filename(title)

                        artist_folder = os.path.join(destination_base_folder, artist)
                        album_folder = os.path.join(artist_folder, album)

                        new_filename = f"{title}{file_ext}"
                        new_file_path = os.path.join(album_folder, new_filename)

                        counter = 1
                        while os.path.exists(new_file_path):
                            new_filename = f"{title} ({counter}){file_ext}"
                            new_file_path = os.path.join(album_folder, new_filename)
                            counter += 1

                        os.makedirs(album_folder, exist_ok=True)
                        shutil.move(file_path, new_file_path)
                        print(f"Moved '{filename}' to '{os.path.relpath(new_file_path, destination_base_folder)}'")
                    except Exception as e:
                        print(f"Error organizing '{filename}': {e}")
                        unorganized_folder = os.path.join(destination_base_folder, "Unorganized")
                        os.makedirs(unorganized_folder, exist_ok=True)
                        shutil.move(file_path, os.path.join(unorganized_folder, filename))
                        print(f"Moved '{filename}' to 'Unorganized' due to error: {e}")

        # Clean up empty directories and __artwork subfolder
        def remove_empty_dirs(path):
            """Recursively remove empty directories."""
            for root, dirs, files in os.walk(path, topdown=False):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        os.rmdir(dir_path)
                        print(f"Removed empty directory: {dir_path}")
                    except OSError:
                        pass

        # Remove __artwork folder
        artwork_folder = os.path.join(source_folder, "__artwork")
        if os.path.exists(artwork_folder) and os.path.isdir(artwork_folder):
            try:
                shutil.rmtree(artwork_folder)
                print(f"Removed __artwork folder: {artwork_folder}")
            except Exception as e:
                print(f"Warning: Could not remove __artwork folder {artwork_folder}: {e}")

        # Remove empty directories in source folder
        remove_empty_dirs(source_folder)

        # Fix permissions on organized files
        os.system(f'chown -R 1000:1000 "{destination_base_folder}"')

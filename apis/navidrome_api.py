import requests
import hashlib
import os
import sys
import shutil
import sqlite3
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
    def __init__(self, root_nd, user_nd, password_nd, music_library_path, target_comment, lastfm_target_comment, album_recommendation_comment=None, llm_target_comment=None, listenbrainz_enabled=False, lastfm_enabled=False, llm_enabled=False, admin_user=None, admin_password=None, navidrome_db_path=None):
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
        self.admin_user = admin_user or ''
        self.admin_password = admin_password or ''
        self.navidrome_db_path = navidrome_db_path or ''

    def _get_navidrome_auth_params(self):
        """Generates authentication parameters for Navidrome."""
        salt = os.urandom(6).hex()
        token = hashlib.md5((self.password_nd + salt).encode('utf-8')).hexdigest()
        return salt, token

    def _get_admin_auth_params(self):
        """Generates authentication parameters using admin credentials.
        Falls back to regular user credentials if admin creds are not configured."""
        user = self.admin_user if self.admin_user else self.user_nd
        password = self.admin_password if self.admin_password else self.password_nd
        salt = os.urandom(6).hex()
        token = hashlib.md5((password + salt).encode('utf-8')).hexdigest()
        return user, salt, token

    def _get_all_songs(self, salt, token):
        """Fetches all songs from Navidrome."""
        url = f"{self.root_nd}/rest/search3.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'trackdrop',
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

    def _get_song_details(self, song_id, salt, token, user=None):
        """Fetches details of a specific song from Navidrome."""
        url = f"{self.root_nd}/rest/getSong.view"
        params = {
            'u': user or self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'trackdrop',
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

    def _check_song_protection(self, song_id):
        """Check if a song is protected from deletion by any user interaction.
        Returns a dict with:
          'protected': bool - whether the song should be kept
          'reasons': list of strings explaining why it's protected (or empty if not)
          'max_rating': int - highest rating across all users
        Checks: ratings >= 4, starred/favorited, in any user's non-recommendation playlist.
        Uses direct SQLite read on Navidrome's DB. Falls back to Subsonic API."""

        result = {'protected': False, 'reasons': [], 'max_rating': 0}
        recommendation_playlist_names = {
            'listenbrainz recommendations', 'last.fm recommendations', 'llm recommendations'
        }

        # Try SQLite direct query first (checks all users)
        if self.navidrome_db_path and os.path.exists(self.navidrome_db_path):
            try:
                conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
                cursor = conn.cursor()

                # Check ratings and starred across all users
                cursor.execute(
                    "SELECT user_id, rating, starred, starred_at "
                    "FROM annotation WHERE item_id = ? AND item_type = 'media_file'",
                    (song_id,)
                )
                for row in cursor.fetchall():
                    user_id, rating, starred, starred_at = row
                    rating = rating or 0
                    if rating >= 4:
                        result['protected'] = True
                        result['reasons'].append(f"rated {rating}/5 by user {user_id[:8]}...")
                    result['max_rating'] = max(result['max_rating'], rating)
                    if starred or starred_at:
                        result['protected'] = True
                        result['reasons'].append(f"starred/favorited by user {user_id[:8]}...")
                        result['max_rating'] = max(result['max_rating'], 5)

                # Check if song is in any non-recommendation playlist
                cursor.execute(
                    "SELECT p.name, p.owner_id FROM playlist p "
                    "JOIN playlist_tracks pt ON p.id = pt.playlist_id "
                    "WHERE pt.media_file_id = ?",
                    (song_id,)
                )
                for row in cursor.fetchall():
                    playlist_name, owner_id = row
                    if playlist_name.lower() not in recommendation_playlist_names:
                        result['protected'] = True
                        result['reasons'].append(f"in playlist '{playlist_name}' (owner {owner_id[:8]}...)")

                conn.close()
                return result
            except Exception as e:
                print(f"  Warning: Could not query Navidrome DB: {e}. Falling back to API.", flush=True)

        # Fallback: check via Subsonic API (regular user + admin only)
        salt, token = self._get_navidrome_auth_params()
        details = self._get_song_details(song_id, salt, token)
        if details:
            starred = details.get("starred")
            user_rating = details.get('userRating', 0)
            if starred:
                result['protected'] = True
                result['reasons'].append(f"starred by {self.user_nd}")
                result['max_rating'] = max(result['max_rating'], 5)
            if user_rating >= 4:
                result['protected'] = True
                result['reasons'].append(f"rated {user_rating}/5 by {self.user_nd}")
            result['max_rating'] = max(result['max_rating'], user_rating)

        if self.admin_user and self.admin_user != self.user_nd:
            admin_user, admin_salt, admin_token = self._get_admin_auth_params()
            admin_details = self._get_song_details(song_id, admin_salt, admin_token, user=admin_user)
            if admin_details:
                starred = admin_details.get("starred")
                admin_rating = admin_details.get('userRating', 0)
                if starred:
                    result['protected'] = True
                    result['reasons'].append(f"starred by {self.admin_user}")
                    result['max_rating'] = max(result['max_rating'], 5)
                if admin_rating >= 4:
                    result['protected'] = True
                    result['reasons'].append(f"rated {admin_rating}/5 by {self.admin_user}")
                result['max_rating'] = max(result['max_rating'], admin_rating)

        return result

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
        Navidrome may store paths relative to its own music folder which can differ
        from TrackDrop's music_library_path mount point.
        """
        print(f"[PATH RESOLVE] Trying to find: '{navidrome_relative_path}'", flush=True)
        print(f"[PATH RESOLVE] Music library: '{self.music_library_path}'", flush=True)

        # Build a list of candidate relative paths to try
        candidates = []

        # Source 1: Navidrome's actual path from the Subsonic API (most reliable)
        if song_details and song_details.get('path'):
            api_path = song_details['path']
            candidates.append(api_path)
            # Strip common Navidrome prefixes to get relative path
            for prefix in ['/music/', '/data/music/', '/data/', '/media/', 'music/']:
                if api_path.startswith(prefix):
                    candidates.append(api_path[len(prefix):])

        # Source 2: Query Navidrome DB for the path
        if self.navidrome_db_path and os.path.exists(self.navidrome_db_path) and song_details:
            nd_id = song_details.get('id', '')
            if nd_id:
                try:
                    conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
                    cursor = conn.cursor()
                    cursor.execute("SELECT path FROM media_file WHERE id = ?", (nd_id,))
                    row = cursor.fetchone()
                    conn.close()
                    if row and row[0]:
                        db_path = row[0]
                        print(f"[PATH RESOLVE] DB path: '{db_path}'", flush=True)
                        candidates.append(db_path)
                        for prefix in ['/music/', '/data/music/', '/data/', '/media/', 'music/']:
                            if db_path.startswith(prefix):
                                candidates.append(db_path[len(prefix):])
                except Exception as e:
                    print(f"[PATH RESOLVE] DB query failed: {e}", flush=True)

        # Source 3: The relative path from the download history
        if navidrome_relative_path:
            candidates.append(navidrome_relative_path)

        # Deduplicate while preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        # Try each candidate joined with music_library_path
        for candidate in unique_candidates:
            # Try as absolute path first
            if os.path.isabs(candidate) and os.path.exists(candidate):
                print(f"[PATH RESOLVE] FOUND as absolute: '{candidate}'", flush=True)
                return candidate
            # Join with music library
            full = os.path.join(self.music_library_path, candidate)
            if os.path.exists(full):
                print(f"[PATH RESOLVE] FOUND: '{full}'", flush=True)
                return full

        # Strategy 2: scan the artist/album directory for files matching the title
        # Try each candidate path to find the right artist/album directory
        for rel_path in unique_candidates:
            path_parts = [p for p in rel_path.split('/') if p]
            if len(path_parts) < 2:
                continue

            # Walk backwards to find a valid artist/album dir
            # The file structure is: [optional_prefix/]artist/album/file.ext
            for start_idx in range(len(path_parts) - 2):
                artist_name = path_parts[start_idx]
                album_name = path_parts[start_idx + 1] if start_idx + 2 < len(path_parts) else None
                if not album_name:
                    continue

                album_dir = os.path.join(self.music_library_path, artist_name, album_name)
                if not os.path.isdir(album_dir):
                    continue

                files = os.listdir(album_dir)
                audio_exts = ('.flac', '.mp3', '.ogg', '.m4a', '.aac', '.wma')
                audio_files = [f for f in files if any(f.lower().endswith(e) for e in audio_exts)]
                print(f"[PATH RESOLVE] Scanning dir '{album_dir}': {audio_files}", flush=True)

                # If only one audio file, that's probably it
                if len(audio_files) == 1:
                    found = os.path.join(album_dir, audio_files[0])
                    print(f"[PATH RESOLVE] FOUND (only audio file in dir): '{found}'", flush=True)
                    return found

                # Try matching by title from song_details
                if song_details:
                    song_title = song_details.get('title', '').lower()
                    for f in audio_files:
                        if song_title and song_title in f.lower():
                            found = os.path.join(album_dir, f)
                            print(f"[PATH RESOLVE] FOUND (title match): '{found}'", flush=True)
                            return found

        # Strategy 3: fallback complex logic
        result = self._find_actual_song_path_fallback(navidrome_relative_path)
        print(f"[PATH RESOLVE] Fallback result: {result}", flush=True)
        return result

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

    @staticmethod
    def _normalize_for_match(text):
        """Normalize a string for fuzzy matching: lowercase, strip punctuation artifacts,
        remove feat./ft./with clauses, and collapse whitespace."""
        import re
        t = text.lower().strip()
        # Remove trailing periods (e.g. "Escapism." -> "escapism")
        t = t.rstrip('.')
        # Remove feat/ft/with clauses: "DJ Snake feat. Justin Bieber" -> "dj snake"
        t = re.sub(r'\s*(feat\.?|ft\.?|featuring|with)\s+.*', '', t)
        # Remove parenthetical/bracket suffixes: "(Radio Edit)", "[Remastered]", "(Explicit)"
        t = re.sub(r'\s*[\(\[].*?[\)\]]', '', t)
        # Collapse whitespace
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def _search_song_in_navidrome(self, artist, title, salt, token, album=None):
        """Search for a song in Navidrome by artist and title. Returns song dict or None.
        Uses multiple search strategies and fuzzy matching to handle feat./ft. artist
        variations and title differences. If album is provided, strongly prefers matches
        from the same album and won't match songs from different albums."""
        import re

        norm_artist = self._normalize_for_match(artist)
        norm_title = self._normalize_for_match(title)
        norm_album = self._normalize_for_match(album) if album else None

        # Build multiple search queries to maximize chances of finding the song
        queries = []
        # 1. Title-only search (most reliable since Navidrome full-text indexes titles well)
        queries.append(title)
        # 2. Normalized artist + title
        queries.append(f"{norm_artist} {norm_title}")
        # 3. Original full query
        queries.append(f"{artist} {title}")

        seen_ids = set()
        all_candidates = []

        for query in queries:
            url = f"{self.root_nd}/rest/search3.view"
            params = {
                'u': self.user_nd,
                't': token,
                's': salt,
                'v': '1.16.1',
                'c': 'trackdrop',
                'f': 'json',
                'query': query,
                'songCount': 20,
                'artistCount': 0,
                'albumCount': 0
            }
            try:
                response = requests.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                songs = data.get('subsonic-response', {}).get('searchResult3', {}).get('song', [])
                for s in songs:
                    sid = s.get('id', '')
                    if sid not in seen_ids:
                        seen_ids.add(sid)
                        all_candidates.append(s)
            except Exception as e:
                print(f"Error searching Navidrome with query '{query}': {e}")

        if not all_candidates:
            return None

        def _score(song):
            """Score a candidate song. Higher = better match."""
            s_artist = self._normalize_for_match(song.get('artist', ''))
            s_title = self._normalize_for_match(song.get('title', ''))
            score = 0

            # Exact normalized title match
            if s_title == norm_title:
                score += 100
            # Title contains or is contained
            elif norm_title in s_title or s_title in norm_title:
                score += 60

            # Exact normalized artist match
            if s_artist == norm_artist:
                score += 100
            # Artist contains or is contained (handles "DJ Snake" in "DJ Snake feat. Justin Bieber")
            elif norm_artist in s_artist or s_artist in norm_artist:
                score += 60
            # Check if any word overlap for multi-artist strings like "VXLLAIN, iGRES, ENXK"
            else:
                artist_words = set(re.split(r'[,&\s]+', norm_artist))
                s_artist_words = set(re.split(r'[,&\s]+', s_artist))
                overlap = artist_words & s_artist_words
                if overlap:
                    score += 30 * len(overlap)

            # Album matching when album filter is provided
            if norm_album:
                s_album = self._normalize_for_match(song.get('album', ''))
                if s_album == norm_album:
                    score += 50
                elif norm_album in s_album or s_album in norm_album:
                    score += 25
                else:
                    # Wrong album — penalize heavily so we don't match remasters, etc.
                    score -= 200

            return score

        # Score all candidates and pick the best
        scored = [(s, _score(s)) for s in all_candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_song, best_score = scored[0]
        # Require at least a title or artist partial match (score >= 60)
        if best_score >= 60:
            return best_song

        # If nothing scored well, don't return a wrong match
        print(f"  No confident match for '{artist} - {title}' (best score: {best_score})")
        return None

    def _find_song_by_path(self, file_path):
        """Look up a song in Navidrome's SQLite DB by file path.
        Returns a dict with 'id' and 'path' keys, or None if not found.
        Handles path prefix differences between TrackDrop and Navidrome mounts."""
        if not self.navidrome_db_path or not os.path.exists(self.navidrome_db_path):
            return None

        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{self.navidrome_db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # The file_path from organize is absolute under TrackDrop's mount (e.g. /app/music/Artist/Album/Track.flac)
            # Navidrome stores paths relative to its own music folder (e.g. /music/trackdrop/Artist/Album/Track.flac)
            # Try multiple path suffixes to match
            candidates = [file_path]

            # Extract relative path after the music library base
            music_bases = ['/app/music/', '/app/music']
            for base in music_bases:
                if file_path.startswith(base):
                    rel = file_path[len(base):]
                    candidates.append(rel)
                    # Navidrome might prepend its own base
                    candidates.append(f"/music/{rel}")
                    candidates.append(f"/music/trackdrop/{rel}")
                    candidates.append(f"trackdrop/{rel}")
                    break

            for path_candidate in candidates:
                cursor.execute("SELECT id, path FROM media_file WHERE path = ?", (path_candidate,))
                row = cursor.fetchone()
                if row:
                    conn.close()
                    return {'id': row[0], 'path': row[1]}

            # Fallback: match by filename suffix (basename within artist/album structure)
            basename = os.path.basename(file_path)
            cursor.execute("SELECT id, path FROM media_file WHERE path LIKE ?", (f"%/{basename}",))
            rows = cursor.fetchall()
            if len(rows) == 1:
                conn.close()
                return {'id': rows[0][0], 'path': rows[0][1]}

            conn.close()
        except Exception as e:
            print(f"  Error looking up song by path in DB: {e}")
        return None

    def _get_playlists(self, salt, token):
        """Get all playlists from Navidrome."""
        url = f"{self.root_nd}/rest/getPlaylists.view"
        params = {
            'u': self.user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'trackdrop',
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
            'c': 'trackdrop',
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
        First removes all existing songs via updatePlaylist songIndexToRemove,
        then adds new ones via createPlaylist if any."""
        # Step 1: Remove all existing songs
        current_songs = self._get_playlist_songs(playlist_id, salt, token)
        if current_songs:
            print(f"  Removing {len(current_songs)} existing songs from playlist (id={playlist_id})...")
            url = f"{self.root_nd}/rest/updatePlaylist.view"
            params = {
                'u': self.user_nd,
                't': token,
                's': salt,
                'v': '1.16.1',
                'c': 'trackdrop',
                'f': 'json',
                'playlistId': playlist_id
            }
            param_list = [(k, v) for k, v in params.items()]
            for i in range(len(current_songs)):
                param_list.append(('songIndexToRemove', i))
            try:
                response = requests.get(url, params=param_list)
                response.raise_for_status()
                data = response.json()
                status = data.get('subsonic-response', {}).get('status')
                if status != 'ok':
                    print(f"  Error removing songs from playlist (id={playlist_id}): {data}")
                    return False
                print(f"  Removed {len(current_songs)} songs from playlist (id={playlist_id}).")
            except Exception as e:
                print(f"  Error removing songs from playlist (id={playlist_id}): {e}")
                return False

        # Step 2: Add new songs if any
        if song_ids:
            print(f"  Adding {len(song_ids)} songs to playlist (id={playlist_id})...")
            url = f"{self.root_nd}/rest/createPlaylist.view"
            params = {
                'u': self.user_nd,
                't': token,
                's': salt,
                'v': '1.16.1',
                'c': 'trackdrop',
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
                if data.get('subsonic-response', {}).get('status') != 'ok':
                    print(f"  Error adding songs to playlist (id={playlist_id}): {data}")
                    return False
            except Exception as e:
                print(f"  Error adding songs to playlist (id={playlist_id}): {e}")
                return False

        print(f"  Updated playlist (id={playlist_id}): now has {len(song_ids)} tracks.")
        return True

    def _start_scan(self, _salt=None, _token=None):
        """Trigger a Navidrome library scan via the Subsonic API.
        Uses admin credentials since startScan requires admin privileges."""
        admin_user, salt, token = self._get_admin_auth_params()
        url = f"{self.root_nd}/rest/startScan.view"
        params = {
            'u': admin_user,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'trackdrop',
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

    def _get_scan_status(self, _salt=None, _token=None):
        """Check if a library scan is in progress.
        Uses admin credentials since getScanStatus requires admin privileges."""
        admin_user, salt, token = self._get_admin_auth_params()
        url = f"{self.root_nd}/rest/getScanStatus.view"
        params = {
            'u': admin_user,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'trackdrop',
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

    def _wait_for_scan(self, timeout=120):
        """Wait for an ongoing library scan to complete."""
        start = time.time()
        while time.time() - start < timeout:
            if not self._get_scan_status():
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
            'c': 'trackdrop',
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
            'c': 'trackdrop',
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

    def update_api_playlists(self, all_recommendations, history_path, downloaded_songs_info=None, file_path_map=None):
        """After downloading, update Navidrome API playlists for each source.
        - Triggers a scan so new files get IDs
        - Groups ALL recommended tracks by source (not just downloaded ones)
        - For each source, finds/creates a playlist and sets its contents
        - Only records actually-downloaded songs in the download history

        Args:
            all_recommendations: ALL songs from the recommendation list (downloaded + pre-existing)
            history_path: Path to the download history JSON
            downloaded_songs_info: Only the songs that were actually downloaded (for history tracking)
            file_path_map: Dict mapping temp download paths to final organized paths (from organize_music_files)
        """
        if downloaded_songs_info is None:
            downloaded_songs_info = []
        if file_path_map is None:
            file_path_map = {}

        salt, token = self._get_navidrome_auth_params()

        # Build a lookup: (artist_lower, title_lower) -> final_file_path for downloaded songs
        downloaded_file_paths = {}
        downloaded_set = set()
        for s in downloaded_songs_info:
            key = (s.get('artist', '').lower(), s.get('title', '').lower())
            downloaded_set.add(key)
            # Map the temp download path to the final organized path
            temp_path = s.get('downloaded_path', '')
            if temp_path and temp_path in file_path_map:
                downloaded_file_paths[key] = file_path_map[temp_path]

        # Trigger scan and wait (so newly downloaded files get IDs)
        if downloaded_songs_info:
            print("Triggering library scan for newly downloaded files...")
            self._start_scan()
            self._wait_for_scan()

        # Group ALL recommended songs by source
        source_map = {
            'listenbrainz': 'ListenBrainz Weekly',
            'last.fm': 'Last.fm Weekly',
            'llm': 'LLM Weekly',
        }

        tracks_by_source = {}
        for song in all_recommendations:
            src = song.get('source', 'Unknown').lower()
            playlist_name = source_map.get(src, f"{song.get('source', 'Unknown')} Weekly")
            if playlist_name not in tracks_by_source:
                tracks_by_source[playlist_name] = []
            tracks_by_source[playlist_name].append(song)

        for playlist_name, songs in tracks_by_source.items():
            print(f"\n--- Updating API playlist: {playlist_name} ---")
            song_ids = []
            for song in songs:
                nd_song = None
                song_key = (song.get('artist', '').lower(), song.get('title', '').lower())
                was_downloaded = song_key in downloaded_set

                # For downloaded songs, try path-based lookup first (most reliable)
                if was_downloaded and song_key in downloaded_file_paths:
                    final_path = downloaded_file_paths[song_key]
                    nd_song = self._find_song_by_path(final_path)
                    if nd_song:
                        print(f"  Found by path: {song['artist']} - {song['title']} (id={nd_song['id']})")

                # Fall back to search-based matching
                if not nd_song:
                    nd_song = self._search_song_in_navidrome(song['artist'], song['title'], salt, token)

                if nd_song:
                    song_ids.append(nd_song['id'])
                    if was_downloaded:
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
                        print(f"  Pre-existing in library: {song['artist']} - {song['title']} (id={nd_song['id']})")
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
            'ListenBrainz': 'ListenBrainz Weekly',
            'Last.fm': 'Last.fm Weekly',
            'LLM': 'LLM Weekly',
        }

        for source_name in list(history.keys()):
            tracks = history.get(source_name, [])
            if not tracks:
                continue

            print(f"\n--- API Cleanup for source: {source_name} ({len(tracks)} tracked downloads) ---")

            playlist_name = playlist_name_map.get(source_name, f"{source_name} Weekly")
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

                # Get song details from Navidrome for rating (check all users)
                song_details = None
                if nd_id:
                    song_details = self._get_song_details(nd_id, salt, token)

                if song_details is None:
                    # Song might have been deleted externally, remove from history
                    print(f"  Song not found in Navidrome: {artist} - {title}. Removing from history.")
                    tracks_to_remove.append(track)
                    continue

                # Check protection across all users
                protection = self._check_song_protection(nd_id)
                user_rating = protection['max_rating']

                if protection['protected']:
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

    async def process_debug_cleanup(self, history_path):
        """Debug cleanup:
        - Only deletes FILES for songs tracked in download history (songs TrackDrop downloaded)
          that are not protected (rated, starred, in a user playlist).
        - Purges ALL songs from recommendation playlists regardless.
        - Only removes successfully-deleted songs from download history.
        No feedback is submitted. Returns a summary dict for the UI."""
        import sys

        print(f"\n{'='*60}", flush=True)
        print(f"[DEBUG CLEANUP] Starting debug cleanup", flush=True)
        print(f"[DEBUG CLEANUP] History path: {history_path}", flush=True)
        print(f"[DEBUG CLEANUP] History file exists: {os.path.exists(history_path)}", flush=True)
        print(f"[DEBUG CLEANUP] Music library path: {self.music_library_path}", flush=True)
        print(f"[DEBUG CLEANUP] Navidrome DB path: {self.navidrome_db_path}", flush=True)
        print(f"{'='*60}", flush=True)

        salt, token = self._get_navidrome_auth_params()
        print(f"[DEBUG CLEANUP] Auth params obtained (salt={salt[:4]}...)", flush=True)

        history = self._load_download_history(history_path)
        print(f"[DEBUG CLEANUP] Loaded history: {len(history)} sources", flush=True)
        if history:
            for src, tracks in history.items():
                print(f"[DEBUG CLEANUP]   Source '{src}': {len(tracks)} tracks", flush=True)
                for t in tracks:
                    print(f"[DEBUG CLEANUP]     - {t.get('artist','')} - {t.get('title','')} | nd_id={t.get('navidrome_id','')} | path={t.get('file_path','')}", flush=True)
        else:
            print(f"[DEBUG CLEANUP] History is EMPTY - no files will be deleted", flush=True)
            print(f"[DEBUG CLEANUP] (Run a playlist generation first to populate the download history)", flush=True)
        sys.stdout.flush()

        summary = {'deleted': [], 'kept': [], 'failed': [], 'playlists_cleared': []}

        playlist_name_map = {
            'ListenBrainz': 'ListenBrainz Weekly',
            'Last.fm': 'Last.fm Weekly',
            'LLM': 'LLM Weekly',
        }

        # Track which history entries to keep (songs that were NOT successfully deleted)
        remaining_history = {}

        # Step 1: Process download history - only delete files we actually downloaded
        if history:
            total_tracks = sum(len(v) for v in history.values())
            print(f"\n[DEBUG CLEANUP] Step 1: Processing {total_tracks} tracked downloads for file deletion", flush=True)
            for source_name in list(history.keys()):
                tracks = history.get(source_name, [])
                if not tracks:
                    continue

                remaining_tracks = []
                print(f"\n[DEBUG CLEANUP] Source: {source_name} ({len(tracks)} tracked downloads)", flush=True)

                for track in tracks:
                    artist = track.get('artist', '')
                    title = track.get('title', '')
                    nd_id = track.get('navidrome_id', '')
                    file_rel_path = track.get('file_path', '')
                    label = f"{artist} - {title}"

                    print(f"[DEBUG CLEANUP]   Checking: {label} (id={nd_id}, path={file_rel_path})", flush=True)

                    if not nd_id:
                        print(f"[DEBUG CLEANUP]     No navidrome_id - keeping in history", flush=True)
                        summary['failed'].append(f"{label} (no navidrome_id)")
                        remaining_tracks.append(track)
                        continue

                    song_details = self._get_song_details(nd_id, salt, token)
                    if song_details is None:
                        print(f"[DEBUG CLEANUP]     Not found in Navidrome (already deleted?) - removing from history", flush=True)
                        summary['deleted'].append(f"{label} (already gone from Navidrome)")
                        continue

                    print(f"[DEBUG CLEANUP]     Song exists in Navidrome: path={song_details.get('path','')}", flush=True)

                    # Check protection across all users
                    protection = self._check_song_protection(nd_id)
                    if protection['protected']:
                        reasons = '; '.join(protection['reasons'])
                        print(f"[DEBUG CLEANUP]     PROTECTED - keeping file, removing from download history (now permanent)", flush=True)
                        print(f"[DEBUG CLEANUP]     Reasons: {reasons}", flush=True)
                        summary['kept'].append(f"{label} ({reasons})")
                        # Don't add to remaining_tracks - remove from history since it's now a permanent library file
                        continue

                    print(f"[DEBUG CLEANUP]     NOT protected (max_rating={protection['max_rating']}) - attempting deletion", flush=True)

                    # Try to find and delete the file
                    file_path = self._find_actual_song_path(file_rel_path, song_details)
                    if file_path and os.path.exists(file_path):
                        if self._delete_song(file_path):
                            print(f"[DEBUG CLEANUP]     DELETED: {file_path}", flush=True)
                            summary['deleted'].append(f"{label} (file deleted: {file_path})")
                            # Don't add to remaining_tracks - successfully deleted
                        else:
                            print(f"[DEBUG CLEANUP]     FAILED to delete: {file_path}", flush=True)
                            summary['failed'].append(f"{label} (delete failed: {file_path})")
                            remaining_tracks.append(track)
                    else:
                        print(f"[DEBUG CLEANUP]     FILE NOT FOUND on disk - cannot delete", flush=True)
                        print(f"[DEBUG CLEANUP]     Tried relative path: {file_rel_path}", flush=True)
                        print(f"[DEBUG CLEANUP]     Resolved to: {file_path}", flush=True)
                        summary['failed'].append(f"{label} (file not found on disk)")
                        remaining_tracks.append(track)

                    sys.stdout.flush()

                if remaining_tracks:
                    remaining_history[source_name] = remaining_tracks
        else:
            print(f"\n[DEBUG CLEANUP] Step 1: SKIPPED - no download history entries to process", flush=True)

        # Step 2: Purge all recommendation playlists (remove all songs, don't delete files)
        print(f"\n[DEBUG CLEANUP] Step 2: Purging recommendation playlists", flush=True)
        for source_name, playlist_name in playlist_name_map.items():
            print(f"[DEBUG CLEANUP]   Looking for playlist: '{playlist_name}'", flush=True)
            existing_playlist = self._find_playlist_by_name(playlist_name, salt, token)
            if not existing_playlist:
                print(f"[DEBUG CLEANUP]   Playlist '{playlist_name}' not found, skipping.", flush=True)
                continue

            song_count = existing_playlist.get('songCount', 0)
            print(f"[DEBUG CLEANUP]   Found playlist '{playlist_name}' (id={existing_playlist['id']}, {song_count} songs) - clearing...", flush=True)
            self._update_playlist(existing_playlist['id'], [], salt, token)
            summary['playlists_cleared'].append(f"{playlist_name} ({song_count} songs)")
            print(f"[DEBUG CLEANUP]   Cleared playlist: {playlist_name}", flush=True)
            sys.stdout.flush()

        # Step 3: Save remaining history (only entries that were NOT successfully deleted)
        self._save_download_history(history_path, remaining_history)
        kept_count = sum(len(v) for v in remaining_history.values())
        print(f"\n[DEBUG CLEANUP] Step 3: Updated download history - {kept_count} entries remaining", flush=True)

        # Step 4: Clear streamrip download databases so deleted songs can be re-downloaded
        print(f"[DEBUG CLEANUP] Step 4: Clearing streamrip download databases", flush=True)
        for db_file in ['/app/temp_downloads/downloads.db', '/app/temp_downloads/failed_downloads.db']:
            if os.path.exists(db_file):
                try:
                    os.remove(db_file)
                    print(f"[DEBUG CLEANUP]   Removed: {db_file}", flush=True)
                except OSError as e:
                    print(f"[DEBUG CLEANUP]   Failed to remove {db_file}: {e}", flush=True)
            else:
                print(f"[DEBUG CLEANUP]   Not found (already clean): {db_file}", flush=True)

        # Step 5: Remove empty folders
        print(f"[DEBUG CLEANUP] Step 5: Removing empty folders from {self.music_library_path}", flush=True)
        from utils import remove_empty_folders
        remove_empty_folders(self.music_library_path)
        print(f"[DEBUG CLEANUP] Empty folder removal completed.", flush=True)

        # Step 6: Trigger library scan
        print(f"[DEBUG CLEANUP] Step 6: Triggering Navidrome library scan", flush=True)
        self._start_scan()

        print(f"\n{'='*60}", flush=True)
        print(f"[DEBUG CLEANUP] === SUMMARY ===", flush=True)
        print(f"[DEBUG CLEANUP]   Successfully deleted: {len(summary['deleted'])}", flush=True)
        for d in summary['deleted']:
            print(f"[DEBUG CLEANUP]     - {d}", flush=True)
        print(f"[DEBUG CLEANUP]   Protected (kept): {len(summary['kept'])}", flush=True)
        for k in summary['kept']:
            print(f"[DEBUG CLEANUP]     - {k}", flush=True)
        print(f"[DEBUG CLEANUP]   Failed/skipped: {len(summary['failed'])}", flush=True)
        for f in summary['failed']:
            print(f"[DEBUG CLEANUP]     - {f}", flush=True)
        print(f"[DEBUG CLEANUP]   Playlists cleared: {', '.join(summary['playlists_cleared']) if summary['playlists_cleared'] else 'none'}", flush=True)
        print(f"[DEBUG CLEANUP]   History entries remaining: {kept_count}", flush=True)
        print(f"{'='*60}", flush=True)
        sys.stdout.flush()

        return summary

    def organize_music_files(self, source_folder, destination_base_folder):
        """
        Organizes music files from a source folder into a destination base folder
        using Artist/Album/filename structure based on metadata.
        Returns a dict mapping original file paths to their new destination paths.
        """
        from mutagen.id3 import ID3, ID3NoHeaderError
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4
        from utils import sanitize_filename

        print(f"\nOrganizing music files from '{source_folder}' to '{destination_base_folder}'...")
        moved_files = {}  # original_path -> new_path

        # Supported audio file extensions
        audio_extensions = ('.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wma')

        for root, dirs, files in os.walk(source_folder):
            for filename in files:
                if filename.lower().endswith(audio_extensions):
                    file_path = os.path.join(root, filename)
                    file_ext = os.path.splitext(filename)[1].lower()

                    try:
                        # Extract metadata based on file type
                        # Use album artist for folder structure (falls back to artist)
                        def _get_tag(tags, key, default=''):
                            val = tags.get(key)
                            if val:
                                return val[0] if isinstance(val, list) else str(val)
                            return default

                        if file_ext == '.mp3':
                            audio = ID3(file_path)
                            # Folder: prefer albumartist (TPE2), fall back to artist (TPE1), then TXXX:ARTISTS
                            folder_artist = str(audio.get('TPE2', [None])[0] or audio.get('TPE1', [None])[0] or 'Unknown Artist')
                            album = str(audio.get('TALB', ['Unknown Album'])[0])
                            title = str(audio.get('TIT2', [os.path.splitext(filename)[0]])[0])
                        elif file_ext == '.flac':
                            audio = FLAC(file_path)
                            folder_artist = _get_tag(audio, 'albumartist') or _get_tag(audio, 'artist') or _get_tag(audio, 'artists', 'Unknown Artist')
                            album = _get_tag(audio, 'album', 'Unknown Album')
                            title = _get_tag(audio, 'title', os.path.splitext(filename)[0])
                        elif file_ext in ('.m4a', '.aac'):
                            audio = MP4(file_path)
                            folder_artist = _get_tag(audio, 'aART') or _get_tag(audio, '\xa9ART', 'Unknown Artist')
                            album = _get_tag(audio, '\xa9alb', 'Unknown Album')
                            title = _get_tag(audio, '\xa9nam', os.path.splitext(filename)[0])
                        elif file_ext in ('.ogg', '.wma'):
                            audio = OggVorbis(file_path)
                            folder_artist = _get_tag(audio, 'albumartist') or _get_tag(audio, 'artist') or _get_tag(audio, 'artists', 'Unknown Artist')
                            album = _get_tag(audio, 'album', 'Unknown Album')
                            title = _get_tag(audio, 'title', os.path.splitext(filename)[0])
                        else:
                            # Fallback for unsupported formats
                            artist = "Unknown Artist"
                            folder_artist = artist
                            album = "Unknown Album"
                            title = os.path.splitext(filename)[0]

                        folder_artist = sanitize_filename(folder_artist)
                        album = sanitize_filename(album)
                        title = sanitize_filename(title)

                        artist_folder = os.path.join(destination_base_folder, folder_artist)
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
                        moved_files[file_path] = new_file_path
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

        return moved_files

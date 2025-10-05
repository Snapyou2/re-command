import requests
import hashlib
import os
import subprocess
import sys
from tqdm import tqdm

class NavidromeAPI:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.root_nd = self.config_manager.get("ROOT_ND")
        self.user_nd = self.config_manager.get("USER_ND")
        self.password_nd = self.config_manager.get("PASSWORD_ND")
        self.music_library_path = self.config_manager.get("MUSIC_LIBRARY_PATH")
        self.target_comment = self.config_manager.get("TARGET_COMMENT")
        self.lastfm_target_comment = self.config_manager.get("LASTFM_TARGET_COMMENT")

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
        """Updates the comment of a song using kid3-cli."""
        try:
            subprocess.run(["kid3-cli", "-c", f"set comment \"{new_comment}\"", file_path], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error updating comment for {file_path}: {e}")
        except FileNotFoundError:
            print(f"kid3-cli not found. Is it installed and in your PATH?")

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
        # Strategy 1: Try the path as-is first (this works for most cases)
        expected_full_path = os.path.join(self.music_library_path, navidrome_relative_path)
        if os.path.exists(expected_full_path):
            return expected_full_path

        # Strategy 2: If we have song details, try reconstructing from metadata
        if song_details:
            artist = song_details.get('artist', '')
            album = song_details.get('album', '')
            title = song_details.get('title', '')

            if artist and album and title:
                # Try to find the file by reconstructing the path from metadata
                reconstructed_path = os.path.join(artist, album, f"{title}.mp3")
                reconstructed_full_path = os.path.join(self.music_library_path, reconstructed_path)
                if os.path.exists(reconstructed_full_path):
                    return reconstructed_full_path

                # Also try with track number if available
                track = song_details.get('track', '')
                if track:
                    reconstructed_path_with_track = os.path.join(artist, album, f"{track} - {title}.mp3")
                    reconstructed_full_path_with_track = os.path.join(self.music_library_path, reconstructed_path_with_track)
                    if os.path.exists(reconstructed_full_path_with_track):
                        return reconstructed_full_path_with_track

        # Strategy 3: Fallback to more complex logic only if needed
        return self._find_actual_song_path_fallback(navidrome_relative_path)

    def _find_actual_song_path_fallback(self, navidrome_relative_path):
        """
        Fallback method using the original complex path resolution logic.
        Only used when the cleaner approach fails.
        """
        # Try common variations
        modified_relative_path_1 = navidrome_relative_path.replace(" - ", " ")
        full_path_1 = os.path.join(self.music_library_path, modified_relative_path_1)
        if os.path.exists(full_path_1):
            return full_path_1

        modified_relative_path_2 = navidrome_relative_path.replace(" ", " - ")
        full_path_2 = os.path.join(self.music_library_path, modified_relative_path_2)
        if os.path.exists(full_path_2):
            return full_path_2

        # Try removing track number prefix (e.g., "03 - Porcelain.mp3" -> "Porcelain.mp3")
        import re
        track_number_pattern = r'^\d{1,2}\s*-\s*(.+)$'
        match = re.match(track_number_pattern, os.path.basename(navidrome_relative_path))
        if match:
            filename_without_number = match.group(1)
            path_without_number = os.path.join(os.path.dirname(navidrome_relative_path), filename_without_number)
            full_path_3 = os.path.join(self.music_library_path, path_without_number)
            if os.path.exists(full_path_3):
                return full_path_3

        # Try with different separators in path
        path_parts = navidrome_relative_path.split('/')
        if len(path_parts) >= 2:
            # Try with just artist/album/filename (no track number)
            filename = os.path.basename(navidrome_relative_path)
            match = re.match(track_number_pattern, filename)
            if match:
                clean_filename = match.group(1)
                clean_path = os.path.join(path_parts[0], path_parts[1], clean_filename)
                full_path_4 = os.path.join(self.music_library_path, clean_path)
                if os.path.exists(full_path_4):
                    return full_path_4

        # Additional case-insensitive and case variations
        # Try case-insensitive matching for each path part
        if len(path_parts) >= 2:
            # Try case-insensitive artist name matching
            artist_lower = path_parts[0].lower()
            album_lower = path_parts[1].lower()

            # Look for directories that match case-insensitively
            try:
                for root_dir in os.listdir(self.music_library_path):
                    if root_dir.lower() == artist_lower:
                        # Found matching artist directory (case-insensitive)
                        artist_actual = root_dir  # Keep original case

                        # Now look for album directory
                        artist_path = os.path.join(self.music_library_path, artist_actual)
                        if os.path.isdir(artist_path):
                            for album_dir in os.listdir(artist_path):
                                if album_dir.lower() == album_lower:
                                    # Found matching album directory
                                    album_actual = album_dir  # Keep original case

                                    # Now try to find the file
                                    album_path = os.path.join(artist_path, album_actual)

                                    # Try with and without track number
                                    filename = os.path.basename(navidrome_relative_path)
                                    match = re.match(track_number_pattern, filename)
                                    if match:
                                        clean_filename = match.group(1)
                                        file_without_number = os.path.join(album_path, clean_filename)
                                        if os.path.exists(file_without_number):
                                            return file_without_number

                                    # Also try the original filename
                                    file_with_path = os.path.join(album_path, filename)
                                    if os.path.exists(file_with_path):
                                        return file_with_path
            except OSError:
                pass

        # Try handling underscore variations in artist names
        # Navidrome might store "Artist_Feat" but actual folder is "Artist"
        if len(path_parts) >= 2:
            artist_part = path_parts[0]
            
            # Try removing everything after underscore (e.g., "Disco Lines_Tinashe" -> "Disco Lines")
            if '_' in artist_part:
                base_artist = artist_part.split('_')[0]
                modified_path = os.path.join(base_artist, *path_parts[1:])
                full_path = os.path.join(self.music_library_path, modified_path)
                if os.path.exists(full_path):
                    return full_path
                
                # Try with the modified path and track number removal
                filename = os.path.basename(modified_path)
                match = re.match(track_number_pattern, filename)
                if match:
                    clean_filename = match.group(1)
                    path_without_number = os.path.join(os.path.dirname(modified_path), clean_filename)
                    full_path_without_number = os.path.join(self.music_library_path, path_without_number)
                    if os.path.exists(full_path_without_number):
                        return full_path_without_number

        return None

    def process_navidrome_library(self, listenbrainz_api=None):
        """Processes the Navidrome library with a progress bar."""
        salt, token = self._get_navidrome_auth_params()
        all_songs = self._get_all_songs(salt, token)
        print(f"Parsing {len(all_songs)} songs from Navidrome to cleanup badly rated songs.")
        print(f"Looking for comments: '{self.target_comment}' (ListenBrainz) and '{self.lastfm_target_comment}' (Last.fm)")

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

            # Also check the actual file's ID3 tags for comment using mutagen
            actual_comment = ""
            try:
                from mutagen.id3 import ID3, COMM, ID3NoHeaderError

                # Only process MP3 files with mutagen (ID3 tags are MP3-specific)
                if song_path.lower().endswith('.mp3'):
                    audio = ID3(song_path)

                    # First, try to get all COMM frames (including language-specific ones)
                    comm_frames = audio.getall('COMM')

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

                    # If we still don't have a comment, try direct access to specific COMM frames
                    # including language-specific ones like COMM::eng, COMM::fra, etc.
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
                                # Also check description field for language-specific frames
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
                                        song_comment == self.lastfm_target_comment)
            

            
            if has_recommendation_comment and song_path:
                user_rating = song_details.get('userRating', 0)
                
                # Process ListenBrainz recommendations
                if song_comment == self.target_comment and self.config_manager.get("LISTENBRAINZ_ENABLED"):
                    if user_rating >= 4:
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
                        if 'musicBrainzId' in song_details and song_details['musicBrainzId'] and user_rating == 1 and listenbrainz_api:
                            listenbrainz_api.submit_feedback(song_details['musicBrainzId'], 1)

                # Process Last.fm recommendations
                elif song_comment == self.lastfm_target_comment and self.config_manager.get("LASTFM_ENABLED"):
                    if user_rating >= 4:
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
                
                # If no specific service is enabled, just delete the commented songs
                elif not self.config_manager.get("LISTENBRAINZ_ENABLED") and not self.config_manager.get("LASTFM_ENABLED"):
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
            


    def organize_music_files(self, source_folder, destination_base_folder):
        """
        Organizes music files from a source folder into a destination base folder
        using Artist/Album/filename structure based on metadata.
        """
        from mutagen.id3 import ID3, COMM, error
        from utils import sanitize_filename

        print(f"\nOrganizing music files from '{source_folder}' to '{destination_base_folder}'...")
        for entry in os.scandir(source_folder):
            if entry.is_file() and entry.name.endswith(".mp3"):
                mp3_file_path = entry.path
                try:
                    audio = ID3(mp3_file_path)
                    artist = str(audio.get('TPE1', ['Unknown Artist'])[0])
                    album = str(audio.get('TALB', ['Unknown Album'])[0])
                    title = str(audio.get('TIT2', [os.path.splitext(entry.name)[0]])[0])

                    artist = sanitize_filename(artist)
                    album = sanitize_filename(album)
                    title = sanitize_filename(title)

                    artist_folder = os.path.join(destination_base_folder, artist)
                    album_folder = os.path.join(artist_folder, album)
                    
                    new_filename = f"{title}.mp3"
                    new_file_path = os.path.join(album_folder, new_filename)
                    
                    counter = 1
                    while os.path.exists(new_file_path):
                        new_filename = f"{title} ({counter}).mp3"
                        new_file_path = os.path.join(album_folder, new_filename)
                        counter += 1

                    os.makedirs(album_folder, exist_ok=True)
                    os.rename(mp3_file_path, new_file_path)
                    print(f"Moved '{entry.name}' to '{os.path.relpath(new_file_path, destination_base_folder)}'")
                except error.ID3NoHeaderError:
                    print(f"Skipping '{entry.name}': No ID3 tag found.")
                    unorganized_folder = os.path.join(destination_base_folder, "Unorganized")
                    os.makedirs(unorganized_folder, exist_ok=True)
                    os.rename(mp3_file_path, os.path.join(unorganized_folder, entry.name))
                    print(f"Moved '{entry.name}' to 'Unorganized' due to missing ID3 tags.")
                except Exception as e:
                    print(f"Error organizing '{entry.name}': {e}")

        # Clean up the __artwork subfolder before the temp folder gets deleted
        artwork_folder = os.path.join(source_folder, "__artwork")
        if os.path.exists(artwork_folder) and os.path.isdir(artwork_folder):
            try:
                import shutil
                shutil.rmtree(artwork_folder)
                print(f"Removed __artwork folder: {artwork_folder}")
            except Exception as e:
                print(f"Warning: Could not remove __artwork folder {artwork_folder}: {e}")
